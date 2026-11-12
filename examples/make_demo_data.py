"""Write a small synthetic dataset to examples/demo_data/ so the tool can be tried without real images.

Two cases, each with a CT-like and an MR-like volume of the same "phantom" (different grids and
intensities), a structure mask, and a 2D frame sequence with a contrast bolus.
Needs numpy and SimpleITK; Slicer's bundled Python has both:
  /Applications/Slicer.app/Contents/bin/PythonSlicer examples/make_demo_data.py
"""
import json
import os

import numpy as np
import SimpleITK as sitk

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'demo_data')


def phantom(spacing, size, origin, rng, shift):
    z, y, x = np.meshgrid(*[np.arange(n) * s + o for n, s, o in zip(size[::-1], spacing[::-1], origin[::-1])],
                          indexing='ij')
    x, y = x - shift[0], y - shift[1]
    body = (x / 150) ** 2 + (y / 110) ** 2 < 1
    organ = ((x + 40) / 80) ** 2 + (y / 60) ** 2 + (z / 70) ** 2 < 1
    vessel = (np.hypot(x + 40 - 0.3 * z, y) < 4) & organ
    lesion = ((x + 60) ** 2 + (y - 20) ** 2 + (z - 10) ** 2) < 15 ** 2
    noise = rng.normal(0, 1, body.shape)
    return body, organ, vessel, lesion, noise


def save(arr, spacing, origin, path):
    img = sitk.GetImageFromArray(arr)
    img.SetSpacing(spacing)
    img.SetOrigin(origin)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sitk.WriteImage(img, path)


def main():
    rng = np.random.default_rng(0)
    for k, case in enumerate(('case01', 'case02')):
        d = os.path.join(OUT, case)
        shift = (5.0 * k, -3.0 * k)
        sp, size, org = (0.8, 0.8, 5.0), (400, 300, 40), (-160.0, -120.0, -100.0)
        body, organ, vessel, lesion, noise = phantom(sp, size, org, rng, shift)
        ct = np.where(body, 40, -1000) + 60 * organ + 150 * vessel - 50 * lesion + 10 * noise
        save(ct.astype(np.int16), sp, org, f'{d}/ct.nii.gz')
        save((organ & ~lesion).astype(np.uint8), sp, org, f'{d}/organ_mask.nii.gz')
        sp, size, org = (1.3, 1.3, 3.0), (240, 180, 60), (-156.0, -117.0, -90.0)
        body, organ, vessel, lesion, noise = phantom(sp, size, org, rng, (shift[0] + 4, shift[1] + 2))
        mr = 200 * body + 300 * organ + 600 * vessel + 250 * lesion + 15 * noise
        save(np.clip(mr, 0, None).astype(np.float32), sp, org, f'{d}/mr.nii.gz')
        T, H, W = 30, 512, 512
        yy, xx = np.mgrid[:H, :W]
        frames = np.full((T, H, W), 3000.0)
        tree = (np.abs(xx - 256 - 0.4 * (yy - 256)) < 4) | ((np.abs(yy - 200) < 3) & (xx > 256) & (xx < 400))
        for t in range(T):
            arterial = np.exp(-((t - 8) / 3) ** 2)
            late = 1 / (1 + np.exp(-(t - 15) / 2))
            blob = ((xx - 330) ** 2 + (yy - 300) ** 2) < 40 ** 2
            frames[t] -= 1500 * arterial * tree + 400 * late * blob
        frames += rng.normal(0, 30, frames.shape)
        np.savez_compressed(f'{d}/frames.npz', frames=np.clip(frames, 0, 4095).astype(np.uint16))
        json.dump({'pixel_mm': [0.37, 0.37], 'arterial': [5, 11], 'late': [18, 29], 'fps': 6},
                  open(f'{d}/frames.json', 'w'), indent=1)
    print('wrote', OUT)


if __name__ == '__main__':
    main()
