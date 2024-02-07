from pathlib import Path

import cv2
import numpy as np
import matplotlib.pyplot as plt

from scipy.ndimage import gaussian_filter

from image_tools import add_overlay


class HeatmapVisualizer:
    def __init__(self, recording_path):
        self.recording_path = Path(recording_path)

    def save_full_heatmap(self, scale=0.25, detail=0.005):
        self._process_image(
            self.recording_path.parent / 'data' / 'webpage-aois' / 'tab-0' / 'gazes.csv',
            self.recording_path.parent / 'screenshots' / '0-0' / 'full.png',
            scale, detail
        )

    def save_aoi_heatmaps(self, scale=0.25, detail=0.025):
        for aoi_screenshot in (self.recording_path.parent / 'screenshots').glob('*/aois/*.png'):
            self.save_aoi_heatmap(aoi_screenshot.stem, scale, detail)

    def save_aoi_heatmap(self, aoi_name, scale=0.25, detail=0.025):
        self._process_image(
            self.recording_path.parent / 'data' / 'webpage-aois' / 'tab-0' / f'aoi-{aoi_name}.csv',
            self.recording_path.parent / 'screenshots' / '0-0' / 'aois' / f'{aoi_name}.png',
            scale, detail
        )

    def _process_image(self, gaze_data_path, screenshot_path, scale=0.25, detail=0.01):

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

if __name__ == '__main__':
    import sys
    visualizer = HeatmapVisualizer(sys.argv[1])
    visualizer.save_full_heatmap(scale=1.0)
    visualizer.save_aoi_heatmaps(scale=1.0)
