"""
Interactive viewer for image/mask pairs stored in separate folders.

    python view_masks.py <images_folder> <masks_folder>

- Expected naming:
    * Images: xxxx_0000.nii.gz (takes first channel per case)
    * Masks:  xxxx.nii.gz (or .nii fallback)
- Shows three panes: MRI, mask, and MRI + translucent mask overlay.
- Navigation:
    * Scroll wheel: next/prev case; hold Shift + scroll to move slices.
    * Arrow keys: up/down = slice +/- 1; left/right = prev/next case.
"""

import nibabel as nib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from pathlib import Path
from typing import Optional

import sys

# Folders for images (xxxx_0000.nii.gz) and masks (xxxx.nii.gz)
# Usage: python view_masks.py <images_folder> <masks_folder>
# Defaults to the example data in this repo.
_REPO = Path(__file__).resolve().parent.parent
IMAGES_ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else _REPO / "examples" / "input"
LABELS_ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else _REPO / "examples" / "expected_output"


def _strip_nii_suffix(name: str) -> str:
    if name.endswith(".nii.gz"):
        return name[:-7]
    if name.endswith(".nii"):
        return name[:-4]
    return name


def _extract_case_id(image_path: Path) -> str:
    """Extract case_xxxx from case_xxxx_0000.nii.gz-style filenames."""
    base = _strip_nii_suffix(image_path.name)
    parts = base.split("_")
    if len(parts) < 2:
        raise ValueError(f"Image filename does not match expected pattern: {image_path}")
    return "_".join(parts[:-1])


def _find_label(case_id: str) -> Optional[Path]:
    for ext in (".nii.gz", ".nii"):
        candidate = LABELS_ROOT / f"{case_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def _discover_cases():
    """Pair images and labels across the two roots; take the first channel per case."""
    if not IMAGES_ROOT.exists():
        raise FileNotFoundError(f"Images root not found: {IMAGES_ROOT}")
    if not LABELS_ROOT.exists():
        raise FileNotFoundError(f"Labels root not found: {LABELS_ROOT}")

    cases = []
    seen = set()
    for img_path in sorted(IMAGES_ROOT.glob("*_*.nii*")):
        try:
            case_id = _extract_case_id(img_path)
        except ValueError:
            continue
        if case_id in seen:
            continue
        label_path = _find_label(case_id)
        if not label_path:
            print(f"Skipping {img_path.name}: no label found for {case_id}")
            continue
        cases.append((case_id, img_path, label_path))
        seen.add(case_id)

    if not cases:
        raise FileNotFoundError("No matching image/label pairs found.")
    return cases


def _load_case(image_path: Path, label_path: Path):
    """Load MRI and mask; squeeze channels; ensure a z dimension exists."""
    img = nib.load(image_path)
    lbl = nib.load(label_path)
    img_data = np.squeeze(img.get_fdata())
    lbl_data = np.squeeze(lbl.get_fdata())

    if img_data.ndim == 4:
        img_data = img_data[..., 0]
    if lbl_data.ndim == 4:
        lbl_data = lbl_data[..., 0]

    if img_data.ndim == 2:
        img_data = img_data[:, :, np.newaxis]
    if lbl_data.ndim == 2:
        lbl_data = lbl_data[:, :, np.newaxis]

    return img_data, lbl_data, img, lbl


def main():
    cases = _discover_cases()

    cache = {}
    state = {"case_idx": 0, "slice": None}

    # Red overlay — solid red at 45% opacity in the colormap,
    # then further softened by alpha=0.75 on the imshow call.
    # Combined effective opacity ~0.34, which keeps anatomy visible
    # while the mask region is clearly highlighted.
    overlay_cmap = ListedColormap([
        [0, 0, 0, 0],           # background fully transparent
        [1.0, 0.0, 0.0, 0.45]   # solid red, 45% opacity
    ])

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    axes[0].set_title("MRI")
    axes[1].set_title("Mask")
    axes[2].set_title("MRI + Mask (overlay)")
    for ax in axes:
        ax.axis("off")
    title_txt = fig.suptitle("")

    def _current_data():
        case_id, img_path, lbl_path = cases[state["case_idx"]]
        if case_id not in cache:
            cache[case_id] = _load_case(img_path, lbl_path)
        return cache[case_id], case_id

    def _draw():
        (img_data, lbl_data, img_ni, lbl_ni), case_id = _current_data()
        z_size = img_data.shape[2]

        if state["slice"] is None or state["slice"] >= z_size:
            state["slice"] = z_size // 2
        idx = max(0, min(z_size - 1, state["slice"]))

        img_slice = img_data[:, :, idx]
        lbl_slice = lbl_data[:, :, idx]

        # --- PANEL 1: MRI ---
        axes[0].cla()
        axes[0].set_title("MRI")
        axes[0].axis("off")
        axes[0].imshow(img_slice.T, cmap="gray", origin="lower")

        # --- PANEL 2: RAW MASK ---
        axes[1].cla()
        axes[1].set_title("Mask")
        axes[1].axis("off")
        axes[1].imshow(lbl_slice.T, cmap="gray", origin="lower")

        # --- PANEL 3: MRI + OVERLAY ---
        axes[2].cla()
        axes[2].set_title("MRI + Mask (overlay)")
        axes[2].axis("off")
        axes[2].imshow(img_slice.T, cmap="gray", origin="lower")

        # Binary mask overlay
        mask_binary = (lbl_slice.T > 0.5)
        axes[2].imshow(mask_binary, cmap=overlay_cmap, origin="lower", alpha=0.75, interpolation="none")

        # Title
        title_txt.set_text(
            f"{case_id} | slice {idx + 1}/{z_size} "
            f"| img axcodes {nib.orientations.aff2axcodes(img_ni.affine)} "
            f"| lbl axcodes {nib.orientations.aff2axcodes(lbl_ni.affine)}"
        )

        fig.canvas.draw_idle()

    def _scroll(event):
        if event.step == 0:
            return
        if event.key == "shift":
            state["slice"] = (state["slice"] or 0) + int(event.step)
        else:
            state["case_idx"] = (state["case_idx"] + int(event.step)) % len(cases)
            state["slice"] = None
        _draw()

    def _key(event):
        if event.key == "up":
            state["slice"] = (state["slice"] or 0) + 1
        elif event.key == "down":
            state["slice"] = (state["slice"] or 0) - 1
        elif event.key == "right":
            state["case_idx"] = (state["case_idx"] + 1) % len(cases)
            state["slice"] = None
        elif event.key == "left":
            state["case_idx"] = (state["case_idx"] - 1) % len(cases)
            state["slice"] = None
        else:
            return
        _draw()

    fig.canvas.mpl_connect("scroll_event", _scroll)
    fig.canvas.mpl_connect("key_press_event", _key)

    _draw()
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()