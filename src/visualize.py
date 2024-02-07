from pathlib import Path

import cv2
import numpy as np
import matplotlib.pyplot as plt

from scipy.ndimage import gaussian_filter

from image_tools import add_overlay


class HeatmapVisualizer:
    def __init__(self, recording_path):
        self.recording_path = Path(recording_path)

    def process_full_image(self, scale=0.25, detail=0.01):
        data_path = self.recording_path.parent / 'data' / 'webpage-aois'
        for tab_gazes_csv in data_path.glob('*/gazes.csv'):
            tab_folder = tab_gazes_csv.parent
            tab_id = tab_folder.name.split('-')[-1]

            tab_image = cv2.imread(str(self.recording_path.parent / 'screenshots' / f'{tab_id}-0' / 'full.png'))
            heatmap = np.zeros(tab_image.shape[:2])

            data = np.genfromtxt(tab_gazes_csv, delimiter=',', names=True)

            hist_dims = (
                int(tab_image.shape[0]*scale),
                int(tab_image.shape[1]*scale),
            )
            gaze_on_surf_x = data['page_x_px'] / (hist_dims[1] / scale)
            gaze_on_surf_y = data['page_y_px'] / (hist_dims[0] / scale)

            # make the histogram
            hist, _, _ = np.histogram2d(
                gaze_on_surf_y,
                gaze_on_surf_x,
                range=[[0, 1.0], [0, 1.0]],
                bins=hist_dims
            )

            # apply gaussian blur
            filter_h = int(detail * hist_dims[0]) // 2 * 2 + 1
            filter_w = int(detail * hist_dims[1]) // 2 * 2 + 1
            heatmap = gaussian_filter(hist, sigma=(filter_w, filter_h), order=0)

            # normalize
            heatmap /= np.max(heatmap)

            # scale to image size
            heatmap = cv2.resize(heatmap, (tab_image.shape[1], tab_image.shape[0]))

            # apply heatmap colors
            cmap_func = plt.get_cmap('jet')
            heatmap_image = (cmap_func(heatmap) * 255).astype(np.uint8)

            # add alpha channel
            heatmap_image[:,:,3] = heatmap * 255

            # write images
            cv2.imwrite(str(tab_folder / 'heatmap-full-transparent.png'), heatmap_image)

            screenshot = cv2.imread(str(self.recording_path.parent / 'screenshots' / f'{tab_id}-0' / 'full.png'))
            overlaid = add_overlay(screenshot, heatmap_image)
            cv2.imwrite(str(tab_folder / 'heatmap-full-overlaid.png'), overlaid)

if __name__ == '__main__':
    import sys
    visualizer = HeatmapVisualizer(sys.argv[1])
    visualizer.process_full_image()
