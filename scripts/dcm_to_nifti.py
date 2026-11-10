"""
DICOM to NIfTI Converter
------------------------
Handles:
  - Extension-less DICOM files (IM_0001 style)
  - Large high-res volumes (e.g. 384 x 384 x 384)
  - Automatically detects single 3D volume vs 4D time series
  - Enhanced / multi-frame DICOM (all slices in one file)

Requirements:
    pip install pydicom nibabel numpy
"""

import numpy as np
import pydicom
import nibabel as nib
from pathlib import Path


def is_dicom(filepath: Path) -> bool:
    """Check if a file is a valid DICOM by reading its magic bytes (no extension needed)."""
    try:
        with open(filepath, 'rb') as f:
            f.seek(128)
            return f.read(4) == b'DICM'
    except Exception:
        return False


def load_all_slices(dicom_dir: Path) -> list:
    """Load all valid DICOM files from a directory, including extension-less files."""
    print("Scanning for DICOM files (including extension-less)...")
    slices = []

    all_files = sorted(dicom_dir.iterdir())
    for f in all_files:
        if f.is_file() and is_dicom(f):
            try:
                ds = pydicom.dcmread(str(f))
                slices.append(ds)
            except Exception as e:
                print(f"  Warning: could not read {f.name}: {e}")

    print(f"  Found {len(slices)} valid DICOM file(s).")
    return slices


def sort_slices(slices: list) -> list:
    """Sort slices into correct spatial order."""
    try:
        slices.sort(key=lambda s: (
            int(getattr(s, 'TemporalPositionIdentifier', 1)),
            float(s.ImagePositionPatient[2])
        ))
        print("  Sorted by TemporalPositionIdentifier + ImagePositionPatient (z).")
    except AttributeError:
        try:
            slices.sort(key=lambda s: int(s.InstanceNumber))
            print("  Sorted by InstanceNumber.")
        except AttributeError:
            print("  Warning: could not sort slices — using file order.")
    return slices


def detect_volumes(slices: list):
    """
    Detect whether data is a single 3D volume or a 4D time series.
    Returns (slices_per_volume, n_volumes).
    """
    try:
        temporal_ids = sorted(set(int(s.TemporalPositionIdentifier) for s in slices))
        n_volumes = len(temporal_ids)
        if n_volumes > 1:
            slices_per_volume = len(slices) // n_volumes
            print(f"  Detected 4D data: {n_volumes} volumes x {slices_per_volume} slices.")
            return slices_per_volume, n_volumes
    except AttributeError:
        pass

    try:
        z_positions = sorted(set(round(float(s.ImagePositionPatient[2]), 4) for s in slices))
        slices_per_volume = len(z_positions)
        n_volumes = len(slices) // slices_per_volume
        if n_volumes > 1:
            print(f"  Detected 4D data: {n_volumes} volumes x {slices_per_volume} slices.")
        else:
            print(f"  Detected 3D data: {slices_per_volume} slices, single volume.")
        return slices_per_volume, n_volumes
    except AttributeError:
        pass

    print("  Could not detect volume structure — treating as single 3D volume.")
    return len(slices), 1


def build_affine(slices: list) -> np.ndarray:
    """Build a NIfTI affine matrix from DICOM spatial metadata."""
    ref = slices[0]
    try:
        row_cosine   = np.array(ref.ImageOrientationPatient[:3], dtype=float)
        col_cosine   = np.array(ref.ImageOrientationPatient[3:], dtype=float)
        slice_cosine = np.cross(row_cosine, col_cosine)

        row_spacing, col_spacing = [float(x) for x in ref.PixelSpacing]

        if len(slices) > 1:
            z0 = float(slices[0].ImagePositionPatient[2])
            z1 = float(slices[1].ImagePositionPatient[2])
            slice_spacing = abs(z1 - z0)
        else:
            slice_spacing = float(getattr(ref, 'SliceThickness', 1.0))

        origin = np.array(ref.ImagePositionPatient, dtype=float)

        affine = np.eye(4)
        affine[:3, 0] = row_cosine   * row_spacing
        affine[:3, 1] = col_cosine   * col_spacing
        affine[:3, 2] = slice_cosine * slice_spacing
        affine[:3, 3] = origin

        print("  Affine built from DICOM spatial metadata.")
        return affine

    except AttributeError:
        print("  Warning: spatial metadata missing — using identity affine.")
        return np.eye(4)


def dicom_folder_to_nifti(dicom_dir: str, output_path: str = None) -> str:
    """
    Convert a folder of extension-less DICOM files to NIfTI.
    Automatically handles 3D, 4D, and enhanced multi-frame DICOM.

    Args:
        dicom_dir:    Path to folder containing DICOM files.
        output_path:  Output .nii.gz file path (must end in .nii.gz).

    Returns:
        Path to the saved NIfTI file.
    """
    dicom_dir = Path(dicom_dir)

    # 1. Load and sort
    slices = load_all_slices(dicom_dir)
    if not slices:
        raise FileNotFoundError(f"No valid DICOM files found in: {dicom_dir}")

    slices = sort_slices(slices)

    # 2. Detect 3D vs 4D
    slices_per_volume, n_volumes = detect_volumes(slices)

    # 3. Build affine
    first_vol_slices = slices[:slices_per_volume]
    affine = build_affine(first_vol_slices)

    # 4. Stack pixel data
    print("Stacking pixel data...")
    first = slices[0].pixel_array
    print(f"  First file pixel_array shape: {first.shape}")

    # --- Enhanced / multi-frame DICOM: all slices packed into one file ---
    if first.ndim >= 3:
        print("  Detected enhanced (multi-frame) DICOM — extracting frames directly.")
        volume = np.squeeze(first).astype(np.float32)

        if volume.ndim == 3:
            # (slices, rows, cols) -> (rows, cols, slices)
            volume = np.transpose(volume, (1, 2, 0))
        elif volume.ndim == 4:
            # (slices, rows, cols, extra) -> (rows, cols, slices, extra)
            volume = np.transpose(volume, (1, 2, 0, 3))

        print(f"  Final volume shape: {volume.shape}")

    # --- Standard DICOM: one slice per file ---
    elif n_volumes == 1:
        rows, cols = first.shape
        volume = np.zeros((rows, cols, slices_per_volume), dtype=np.float32)
        for i, s in enumerate(slices):
            volume[:, :, i] = s.pixel_array.astype(np.float32)
            if (i + 1) % 50 == 0:
                print(f"  Loaded {i+1}/{slices_per_volume} slices...")
        print(f"  Final volume shape: {volume.shape}")

    else:
        rows, cols = first.shape
        volume = np.zeros((rows, cols, slices_per_volume, n_volumes), dtype=np.float32)
        for vol_idx in range(n_volumes):
            for sl_idx in range(slices_per_volume):
                flat_idx = vol_idx * slices_per_volume + sl_idx
                volume[:, :, sl_idx, vol_idx] = slices[flat_idx].pixel_array.astype(np.float32)
            print(f"  Loaded volume {vol_idx+1}/{n_volumes}...")
        print(f"  Final volume shape: {volume.shape}")

    # 5. Save
    if output_path is None:
        output_path = dicom_dir.parent / (dicom_dir.name + ".nii.gz")
    else:
        output_path = Path(output_path)
        # If user gave a folder path instead of a file path, add a filename
        if output_path.suffix not in ('.gz', '.nii'):
            output_path = output_path / "output.nii.gz"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Saving NIfTI to {output_path} ...")
    nifti_img = nib.Nifti1Image(volume, affine)
    nib.save(nifti_img, str(output_path))

    print(f"\n✓ Done! Saved to: {output_path}")
    return str(output_path)


# ------------------------------------------------------------------ #
# Usage: python dcm_to_nifti.py <dicom_folder> [output.nii.gz]
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python dcm_to_nifti.py <dicom_folder> [output.nii.gz]")
        sys.exit(1)

    dicom_folder_to_nifti(
        dicom_dir   = sys.argv[1],
        output_path = sys.argv[2] if len(sys.argv) > 2 else None
    )