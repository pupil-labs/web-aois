from pathlib import Path

import cv2
import numpy as np
import matplotlib.pyplot as plt

from scipy.ndimage import gaussian_filter

from .image_tools import add_overlay


class HeatmapVisualizer:
    def __init__(self, data_path, screenshot_path):
        self.data_path = Path(data_path)
        self.screenshot_path = Path(screenshot_path)

    def _iter_data_files(self, pattern):
        # Support both ".../output" and ".../output/tab-0" input layouts.
        if self.data_path.is_dir() and self.data_path.name.startswith("tab-"):
            return list(self.data_path.glob(pattern))
        return list(self.data_path.glob(f"tab-*/{pattern}"))

    def save_full_heatmap(self, scale=0.25, detail=0.005, source="gaze"):
        file_name = "gazes.csv" if source == "gaze" else "fixations.csv"
        data_files = self._iter_data_files(file_name)
        if not data_files:
            print(f"No full-page {source} files found under {self.data_path}")
            return

        for gaze_data_file in data_files:
            self._save_heatmap(
                gaze_data_file,
                self.screenshot_path / 'full-page.png',
                scale, detail
            )

    def save_aoi_heatmaps(self, scale=0.25, detail=0.025, source="gaze"):
        if source == "gaze":
            aoi_files = [
                path
                for path in self._iter_data_files('aoi-*.csv')
                if not path.name.startswith('aoi-fixations-')
            ]
        else:
            aoi_files = self._iter_data_files('aoi-fixations-*.csv')

        if not aoi_files:
            print(f"No AOI {source} files found under {self.data_path}")
            return

        for gaze_data_file in aoi_files:
            self.save_aoi_heatmap(gaze_data_file, scale, detail)

    def _aoi_screenshot_name(self, data_file_path):
        stem = data_file_path.stem
        if stem.startswith('aoi-fixations-'):
            aoi_name = stem[len('aoi-fixations-'):]
            return f"aoi-{aoi_name}.png"
        return f"{stem}.png"

    def save_aoi_heatmap(self, gaze_data_file, scale=0.25, detail=0.025):
        self._save_heatmap(
            gaze_data_file,
            self.screenshot_path / self._aoi_screenshot_name(gaze_data_file),
            scale, detail
        )

    def _save_heatmap(self, gaze_data_path, screenshot_path, scale=0.25, detail=0.01):
        screenshot = cv2.imread(str(screenshot_path))
        if screenshot is None:
            print(f"Skipping {gaze_data_path}: missing screenshot {screenshot_path}")
            return

        heatmap = np.zeros(screenshot.shape[:2])

        data = np.genfromtxt(gaze_data_path, delimiter=',', names=True)
        if data.size == 0:
            print(f"Skipping {gaze_data_path}: no gaze samples")
            return

        hist_dims = (
            int(screenshot.shape[0]*scale),
            int(screenshot.shape[1]*scale),
        )

        if data.dtype.names is None:
            print(f"Skipping {gaze_data_path}: invalid CSV format")
            return

        if 'page_x_px' in data.dtype.names:
            xy_keys = ('page_x_px', 'page_y_px')
        else:
            xy_keys = ('x_px', 'y_px')

        if xy_keys[0] not in data.dtype.names or xy_keys[1] not in data.dtype.names:
            print(f"Skipping {gaze_data_path}: missing columns {xy_keys}")
            return

        gaze_on_surf_x = data[xy_keys[0]] / (hist_dims[1] / scale)
        gaze_on_surf_y = data[xy_keys[1]] / (hist_dims[0] / scale)

        # make the histogram
        hist, _, _ = np.histogram2d(
            gaze_on_surf_y,
            gaze_on_surf_x,
            range=[[0, 1.0], [0, 1.0]],
            bins=hist_dims
        )

        # apply gaussian blur
        heatmap = gaussian_filter(hist, sigma=15, order=0)

        # normalize
        if np.max(heatmap) <= 0:
            print(f"Skipping {gaze_data_path}: all samples out of view")
            return
        heatmap /= np.max(heatmap)

        # scale to image size
        heatmap = cv2.resize(heatmap, (screenshot.shape[1], screenshot.shape[0]))

        # apply heatmap colors
        cmap_func = plt.get_cmap('jet')
        heatmap_image = (cmap_func(heatmap) * 255).astype(np.uint8)

        # add alpha channel
        heatmap_image[:,:,3] = heatmap * 255

        # write images
        destination = str(gaze_data_path.parent / f'heatmap-{gaze_data_path.stem}-transparent.png')
        cv2.imwrite(destination, heatmap_image)
        print('Saved', destination)

        overlaid = add_overlay(screenshot, heatmap_image)
        destination = str(gaze_data_path.parent / f'heatmap-{gaze_data_path.stem}-overlaid.png')
        cv2.imwrite(destination, overlaid)
        print('Saved', destination)

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate gaze or fixation heatmaps from web AOI exports."
    )
    parser.add_argument("data_path", help="Path to output directory or tab directory")
    parser.add_argument("screenshot_path", help="Path to screenshot directory")
    parser.add_argument(
        "--source",
        choices=["gaze", "fixation"],
        default="gaze",
        help="Heatmap source data (default: gaze)",
    )
    args = parser.parse_args()

    visualizer = HeatmapVisualizer(args.data_path, args.screenshot_path)
    visualizer.save_full_heatmap(scale=1.0, source=args.source)
    visualizer.save_aoi_heatmaps(scale=1.0, source=args.source)

if __name__ == '__main__':
    main()
