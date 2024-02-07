import numpy as np
import cv2

def scale_and_center(image, new_width, new_height):
	old_height, old_width = image.shape[:2]

	if old_width == new_width and old_height == new_height:
		return image

	# Calculate the aspect ratios of the original and new dimensions
	old_aspect_ratio = old_width / old_height
	new_aspect_ratio = new_width / new_height

	# Calculate scaling factors for width and height
	if new_aspect_ratio > old_aspect_ratio:
		scale_factor = new_height / old_height
	else:
		scale_factor = new_width / old_width

	# Calculate new dimensions
	scaled_width = int(old_width * scale_factor)
	scaled_height = int(old_height * scale_factor)

	# Resize the image using the calculated dimensions
	resized_image = cv2.resize(image, (scaled_width, scaled_height))

	# Calculate black bar dimensions
	padding = (
		(new_width - scaled_width) // 2,
		(new_height - scaled_height) // 2,
	)

	# Create a black canvas with the new dimensions
	canvas = np.zeros((new_height, new_width, 3), dtype=np.uint8)

	# Place the resized image onto the canvas, centering it
	x1 = padding[0]
	x2 = padding[0] + scaled_width
	y1 = padding[1]
	y2 = padding[1] + scaled_height

	canvas[y1:y2, x1:x2] = resized_image

	return canvas

def add_overlay(background, overlay, position=(0, 0)):
	# Extract the alpha channel from the overlay image
	overlay_alpha = overlay[:, :, 3] / 255.0

	# Extract the overlay image without alpha channel
	overlay_image = overlay[:, :, :3]

	# Calculate the region of interest (ROI) in the background image
	bg_height, bg_width = background.shape[:2]
	overlay_height, overlay_width = overlay.shape[:2]

	# Calculate the coordinates of the ROI
	x, y = position
	x1 = max(0, x)
	y1 = max(0, y)
	x2 = min(x1 + overlay_width, bg_width)
	y2 = min(y1 + overlay_height, bg_height)

	# Calculate the coordinates of the overlay image
	overlay_x1 = x1 - x
	overlay_y1 = y1 - y
	overlay_x2 = overlay_x1 + (x2 - x1)
	overlay_y2 = overlay_y1 + (y2 - y1)

	# Adjust the ROI coordinates if overlay exceeds bounds
	if x < 0:
		overlay_x1 -= x
		overlay_x2 -= x
	if y < 0:
		overlay_y1 -= y
		overlay_y2 -= y

	# Adjust the overlay image size if overlay exceeds bounds
	overlay_x2 = min(overlay_x2, overlay_width)
	overlay_y2 = min(overlay_y2, overlay_height)

	# Resize the overlay image to match the ROI size
	overlay_image = overlay_image[
		round(overlay_y1):round(overlay_y2),
		round(overlay_x1):round(overlay_x2)
	]
	overlay_alpha = overlay_alpha[
		round(overlay_y1):round(overlay_y2),
		round(overlay_x1):round(overlay_x2)
	]

	# Resize the ROI in the background image to match the overlay size
	background_roi = background[
		round(y1):round(y2),
		round(x1):round(x2)
	]

	# Apply the overlay using alpha blending
	overlay_alpha = np.stack(3*[overlay_alpha], axis=2)
	blended_overlay = cv2.multiply(overlay_alpha, overlay_image.astype(float))
	blended_background = cv2.multiply(1.0 - overlay_alpha, background_roi.astype(float))
	blended_result = cv2.add(blended_overlay, blended_background)

	# Convert the blended result back to uint8 format
	blended_result = blended_result.astype(np.uint8)

	# Update the background image with the blended result
	background[
		round(y1):round(y2),
		round(x1):round(x2)
	] = blended_result

	return background


def draw_text(mat, text, pos=(20, 30), font=cv2.FONT_HERSHEY_COMPLEX, size=1, color=(255, 255, 255), width=2):
	result = cv2.putText(mat, text, pos, font, size, (0, 0, 0), width*3, cv2.LINE_AA)
	result = cv2.putText(result, text, pos, font, size, color, width, cv2.LINE_AA)

	return result