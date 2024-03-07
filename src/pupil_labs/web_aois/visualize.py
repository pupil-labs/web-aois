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

    def save_full_heatmap(self, scale=0.25, detail=0.005):
        for gaze_data_file in self.data_path.glob('tab-*/gazes.csv'):
            self._save_heatmap(
                gaze_data_file,
                self.screenshot_path / 'full-page.png',
                scale, detail
            )

    def save_aoi_heatmaps(self, scale=0.25, detail=0.025):
        for gaze_data_file in self.data_path.glob('tab-*/aoi-*.csv'):
            self.save_aoi_heatmap(gaze_data_file, scale, detail)

    def save_aoi_heatmap(self, gaze_data_file, scale=0.25, detail=0.025):
        self._save_heatmap(
            gaze_data_file,
            self.screenshot_path / gaze_data_file.with_suffix('.png').name,
            scale, detail
        )

    def _save_heatmap(self, gaze_data_path, screenshot_path, scale=0.25, detail=0.01):
        screenshot = cv2.imread(str(screenshot_path))
        heatmap = np.zeros(screenshot.shape[:2])

        data = np.genfromtxt(gaze_data_path, delimiter=',', names=True)

        hist_dims = (
            int(screenshot.shape[0]*scale),
            int(screenshot.shape[1]*scale),
        )

        if 'page_x_px' in data.dtype.names:
            xy_keys = ('page_x_px', 'page_y_px')
        else:
            xy_keys = ('x_px', 'y_px')

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
    import sys
    visualizer = HeatmapVisualizer(sys.argv[1], sys.argv[2])
    visualizer.save_full_heatmap(scale=1.0)
    visualizer.save_aoi_heatmaps(scale=1.0)

if __name__ == '__main__':
    main()
