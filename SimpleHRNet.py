import os

import cv2
import numpy as np
import torch
from torchvision.transforms import transforms

from models_.hrnet import HRNet
from models_.poseresnet import PoseResNet


class SimpleHRNet:
	"""
	SimpleHRNet class.

	The class provides a simple and customizable method to load the HRNet network, load the official pre-trained
	weights, and predict the human pose on single images.
	Multi-person support with the YOLOv3 detector is also included (and enabled by default).
	"""

	def __init__(self,
				 c,
				 nof_joints,
				 checkpoint_path,
				 model_name='HRNet',
				 resolution=(384, 288),
				 interpolation=cv2.INTER_CUBIC,
				 multiperson=True,
				 return_heatmaps=False,
				 return_bounding_boxes=False,
				 max_batch_size=32,
				 yolo_version='v3',
				 yolo_model_def="./models_/detectors/yolo/config/yolov3.cfg",
				 yolo_class_path="./models_/detectors/yolo/data/coco.names",
				 yolo_weights_path="./models_/detectors/yolo/weights/yolov3.weights",
				 device=torch.device("cpu"),
				 enable_tensorrt=False,
				 custom_keypoints=True):
		"""
		Initializes a new SimpleHRNet object.
		HRNet (and YOLOv3) are initialized on the torch.device("device") and
		its (their) pre-trained weights will be loaded from disk.

		Args:
			c (int): number of channels (when using HRNet model) or resnet size (when using PoseResNet model).
			nof_joints (int): number of joints.
			checkpoint_path (str): path to an official hrnet checkpoint or a checkpoint obtained with `train_coco.py`.
			model_name (str): model name (HRNet or PoseResNet).
				Valid names for HRNet are: `HRNet`, `hrnet`
				Valid names for PoseResNet are: `PoseResNet`, `poseresnet`, `ResNet`, `resnet`
				Default: "HRNet"
			resolution (tuple): hrnet input resolution - format: (height, width).
				Default: (384, 288)
			interpolation (int): opencv interpolation algorithm.
				Default: cv2.INTER_CUBIC
			multiperson (bool): if True, multiperson detection will be enabled.
				This requires the use of a people detector (like YOLOv3).
				Default: True
			return_heatmaps (bool): if True, heatmaps will be returned along with poses by self.predict.
				Default: False
			return_bounding_boxes (bool): if True, bounding boxes will be returned along with poses by self.predict.
				Default: False
			max_batch_size (int): maximum batch size used in hrnet inference.
				Useless without multiperson=True.
				Default: 16
			yolo_version (str): version of YOLO. Supported versions: `v3`, `v5`. Used when multiperson is True.
				Default: "v3"
			yolo_model_def (str): path to yolo model definition file. Recommended values:
				- `./models_/detectors/yolo/config/yolov3.cfg` if yolo_version is 'v3'
				- `./models_/detectors/yolo/config/yolov3-tiny.cfg` if yolo_version is 'v3', to use tiny yolo
				- yolov5 model name if yolo_version is 'v5', e.g. `yolov5m` (medium), `yolov5n` (nano)
				- `yolov5m.engine` if yolo_version is 'v5', custom version (e.g. tensorrt model)
				Default: "./models_/detectors/yolo/config/yolov3.cfg"
			yolo_class_path (str): path to yolov3 class definition file.
				Default: "./models_/detectors/yolo/data/coco.names"
			yolo_weights_path (str): path to yolov3 pretrained weights file.
				Default: "./models_/detectors/yolo/weights/yolov3.weights.cfg"
			device (:class:`torch.device`): the hrnet (and yolo) inference will be run on this device.
				Default: torch.device("cpu")
			enable_tensorrt (bool): Enables tensorrt inference for HRnet.
				If enabled, a `.engine` file is expected as `checkpoint_path`.
				Default: False
		"""

		self.c = c
		self.nof_joints = nof_joints
		self.checkpoint_path = checkpoint_path
		self.model_name = model_name
		self.resolution = resolution  # in the form (height, width) as in the original implementation
		self.interpolation = interpolation
		self.multiperson = multiperson
		self.return_heatmaps = return_heatmaps
		self.return_bounding_boxes = return_bounding_boxes
		self.max_batch_size = max_batch_size
		self.yolo_version = yolo_version
		self.yolo_model_def = yolo_model_def
		self.yolo_class_path = yolo_class_path
		self.yolo_weights_path = yolo_weights_path
		self.device = device
		self.enable_tensorrt = enable_tensorrt

		if self.multiperson:
			if self.yolo_version == 'v3':
				from models_.detectors.YOLOv3 import YOLOv3
			elif self.yolo_version == 'v5':
				from models_.detectors.YOLOv5 import YOLOv5
			else:
				raise ValueError('Unsopported YOLO version.')

		if model_name in ('HRNet', 'hrnet'):
			self.model = HRNet(c=c, nof_joints=nof_joints)
		elif model_name in ('PoseResNet', 'poseresnet', 'ResNet', 'resnet'):
			self.model = PoseResNet(resnet_size=c, nof_joints=nof_joints)
		else:
			raise ValueError('Wrong model name.')

		if not self.enable_tensorrt:
			checkpoint = torch.load(checkpoint_path, map_location=self.device)
			if 'model' in checkpoint:
				self.model.load_state_dict(checkpoint['model'])
			else:
				self.model.load_state_dict(checkpoint)

			if 'cuda' in str(self.device):
				print("device: 'cuda' - ", end="")

				if 'cuda' == str(self.device):
					# if device is set to 'cuda', all available GPUs will be used
					print("%d GPU(s) will be used" % torch.cuda.device_count())
					device_ids = None
				else:
					# if device is set to 'cuda:IDS', only that/those device(s) will be used
					print("GPU(s) '%s' will be used" % str(self.device))
					device_ids = [int(x) for x in str(self.device)[5:].split(',')]

				self.model = torch.nn.DataParallel(self.model, device_ids=device_ids)
			elif 'cpu' == str(self.device):
				print("device: 'cpu'")
			else:
				raise ValueError('Wrong device name.')

			self.model = self.model.to(device)
			self.model.eval()
		else:
			from torch2trt import TRTModule
			self.model = TRTModule()
			self.model.load_state_dict(torch.load(checkpoint_path))
			self.model.cuda().eval()

		if not self.multiperson:
			self.transform = transforms.Compose([
				transforms.ToTensor(),
				transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
			])

		else:
			if self.yolo_version == 'v3':
				self.detector = YOLOv3(model_def=yolo_model_def,
									   class_path=yolo_class_path,
									   weights_path=yolo_weights_path,
									   classes=('person',),
									   max_batch_size=self.max_batch_size,
									   device=device)
			else:
				self.detector = YOLOv5(model_def=yolo_model_def,
									   device=device)

			self.transform = transforms.Compose([
				transforms.ToPILImage(),
				transforms.Resize((self.resolution[0], self.resolution[1])),  # (height, width)
				transforms.ToTensor(),
				transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
			])

	def predict(self, image):
		"""
		Predicts the human pose on a single image or a stack of n images.

		Args:
			image (:class:`np.ndarray`):
				the image(s) on which the human pose will be estimated.

				image is expected to be in the opencv format.
				image can be:
					- a single image with shape=(height, width, BGR color channel)
					- a stack of n images with shape=(n, height, width, BGR color channel)

		Returns:
			:class:`np.ndarray` or list:
				a numpy array containing human joints for each (detected) person.

				Format:
					if image is a single image:
						shape=(# of people, # of joints (nof_joints), 3);  dtype=(np.float32).
					if image is a stack of n images:
						list of n np.ndarrays with
						shape=(# of people, # of joints (nof_joints), 3);  dtype=(np.float32).

				Each joint has 3 values: (y position, x position, joint confidence).

				If self.return_heatmaps, the class returns a list with (heatmaps, human joints)
				If self.return_bounding_boxes, the class returns a list with (bounding boxes, human joints)
				If self.return_heatmaps and self.return_bounding_boxes, the class returns a list with
					(heatmaps, bounding boxes, human joints)
		"""
		# Check input image dimensions
		if len(image.shape) == 3:
			keypoints = self._predict_single(image)
		elif len(image.shape) == 4:
			keypoints = self._predict_batch(image)
		else:
			raise ValueError('Wrong image format.')

		# Process for custom keypoints if enabled
		if hasattr(self, "custom_keypoints") and self.custom_keypoints:
			keypoints = self._convert_to_custom_keypoints(keypoints)

		return keypoints



   def _convert_to_custom_keypoints(self, keypoints):
		"""
		Converts the output keypoints from 17 to 14 custom keypoints.

		Args:
			keypoints (:class:`np.ndarray`):
				A numpy array containing 17 keypoints (y, x, confidence) for each person.

		Returns:
			:class:`np.ndarray`:
				A numpy array containing 14 custom keypoints (y, x, confidence) for each person.
		"""
		# Indices for the 14 custom keypoints
		custom_indices = [0, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]

		# Calculate the xyz keypoint (midpoint of left_shoulder and right_shoulder)
		left_shoulder = keypoints[:, 5, :2]  # (y, x) for left_shoulder
		right_shoulder = keypoints[:, 6, :2]  # (y, x) for right_shoulder
		xyz = (left_shoulder + right_shoulder) / 2  # Midpoint (y, x)
		xyz_confidence = (keypoints[:, 5, 2] + keypoints[:, 6, 2]) / 2  # Average confidence

		# Filter for the 14 keypoints
		custom_keypoints = keypoints[:, custom_indices, :]

		# Add xyz as the 14th keypoint
		xyz_with_confidence = np.concatenate([xyz, xyz_confidence[:, np.newaxis]], axis=1)
		custom_keypoints = np.concatenate([custom_keypoints, xyz_with_confidence[:, np.newaxis, :]], axis=1)

		return custom_keypoints



   def _predict_single(self, image):
		if not self.multiperson:
			old_res = image.shape
			if self.resolution is not None:
				image = cv2.resize(
					image,
					(self.resolution[1], self.resolution[0]),  # (width, height)
					interpolation=self.interpolation
				)

			images = self.transform(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)).unsqueeze(dim=0)
			boxes = np.asarray([[0, 0, old_res[1], old_res[0]]], dtype=np.float32)  # [x1, y1, x2, y2]
			heatmaps = np.zeros((1, self.nof_joints, self.resolution[0] // 4, self.resolution[1] // 4),
								dtype=np.float32)

		else:
			detections = self.detector.predict_single(image)
			nof_people = len(detections) if detections is not None else 0
			boxes = np.empty((nof_people, 4), dtype=np.int32)
			# boxes = torch.empty((nof_people, 4),device=self.device)
			images = torch.empty((nof_people, 3, self.resolution[0], self.resolution[1]), device=self.device)  # (height, width)
			heatmaps = np.zeros((nof_people, self.nof_joints, self.resolution[0] // 4, self.resolution[1] // 4),
								dtype=np.float32)

			if detections is not None:
				for i, (x1, y1, x2, y2, conf, cls_conf, cls_pred) in enumerate(detections):
					x1 = int(round(x1.item()))
					x2 = int(round(x2.item()))
					y1 = int(round(y1.item()))
					y2 = int(round(y2.item()))

					# Adapt detections to match HRNet input aspect ratio (as suggested by xtyDoge in issue #14)
					correction_factor = self.resolution[0] / self.resolution[1] * (x2 - x1) / (y2 - y1)

					# Using padding instead of bbox enlargement, this should reduce cross-person keypoint detection
					if correction_factor > 1:
						# increase y side
						center = y1 + (y2 - y1) // 2
						length = int(round((y2 - y1) * correction_factor))
						x1_new = x1
						x2_new = x2
						y1_new = int(center - length // 2)
						y2_new = int(center + length // 2)
						pad = (int(abs(y1_new - y1))), int(abs(y2_new - y2))
						pad_tuple = (pad, (0, 0), (0, 0))

					elif correction_factor < 1:
						center = x1 + (x2 - x1) // 2
						length = int(round((x2 - x1) * 1 / correction_factor))
						x1_new = int(center - length // 2)
						x2_new = int(center + length // 2)
						y1_new = y1
						y2_new = y2
						pad = (abs(x1_new - x1)), int(abs(x2_new - x2))
						pad_tuple = ((0, 0), pad, (0, 0))
					else:
						x1_new = x1
						x2_new = x2
						y1_new = y1
						y2_new = y2
						pad_tuple = None

					image_crop = image[y1:y2, x1:x2, ::-1]
					if pad_tuple is not None:
						image_crop = np.pad(image_crop, pad_tuple)
					images[i] = self.transform(image_crop)
					boxes[i] = [x1_new, y1_new, x2_new, y2_new]
					# boxes[i] = torch.tensor([x1_new, y1_new, x2_new, y2_new])

		if images.shape[0] > 0:
			images = images.to(self.device)

			with torch.no_grad():
				if len(images) <= self.max_batch_size:
					out = self.model(images)

				else:
					out = torch.empty(
						(images.shape[0], self.nof_joints, self.resolution[0] // 4, self.resolution[1] // 4),
						device=self.device
					)
					for i in range(0, len(images), self.max_batch_size):
						out[i:i + self.max_batch_size] = self.model(images[i:i + self.max_batch_size])

			out = out.detach().cpu().numpy()
			pts = np.empty((out.shape[0], out.shape[1], 3), dtype=np.float32)
			# For each human, for each joint: y, x, confidence
			for i, human in enumerate(out):
				heatmaps[i] = human
				for j, joint in enumerate(human):
					pt = np.unravel_index(np.argmax(joint), (self.resolution[0] // 4, self.resolution[1] // 4))
					# 0: pt_y / (height // 4) * (bb_y2 - bb_y1) + bb_y1
					# 1: pt_x / (width // 4) * (bb_x2 - bb_x1) + bb_x1
					# 2: confidences
					pts[i, j, 0] = pt[0] * 1. / (self.resolution[0] // 4) * (boxes[i][3] - boxes[i][1]) + boxes[i][1]
					pts[i, j, 1] = pt[1] * 1. / (self.resolution[1] // 4) * (boxes[i][2] - boxes[i][0]) + boxes[i][0]
					pts[i, j, 2] = joint[pt]

			# # Torch alternative, it could be faster
			# pts = torch.empty((out.shape[0], out.shape[1], 3), dtype=torch.float32,device=self.device)
			# # For each human, for each joint: y, x, confidence
			# (b, indices) = torch.max(out, dim=2)
			# (b, indices) = torch.max(b, dim=2)
			#
			# (c, indicesc) = torch.max(out, dim=3)
			# (c, indicesc) = torch.max(c, dim=2)
			# dims = (self.resolution[0]//4, self.resolution[1]//4)
			# dim1 = torch.tensor(1. / dims[0], device=self.device)
			# dim2 = torch.tensor(1. / dims[1], device=self.device)
			#
			# for i in range(0, out.shape[0]):
			#     pts[i, :, 0] = indicesc[i, :] * dim1 * (boxes[i][3] - boxes[i][1]) + boxes[i][1]
			#     pts[i, :, 1] = indices[i, :] * dim2 * (boxes[i][2] - boxes[i][0]) + boxes[i][0]
			#     pts[i, :, 2] = c[i, :]
			#
			# pts = pts.cpu().numpy()
			# boxes = boxes.cpu().numpy()

		else:
			pts = np.empty((0, 0, 3), dtype=np.float32)

		res = list()
		if self.return_heatmaps:
			res.append(heatmaps)
		if self.return_bounding_boxes:
			res.append(boxes)
		res.append(pts)

		if len(res) > 1:
			return res
		else:
			return res[0]

	def _predict_batch(self, images):
		if not self.multiperson:
			old_res = images[0].shape

			if self.resolution is not None:
				images_tensor = torch.empty(images.shape[0], 3, self.resolution[0], self.resolution[1])
			else:
				images_tensor = torch.empty(images.shape[0], 3, images.shape[1], images.shape[2])

			for i, image in enumerate(images):
				if self.resolution is not None:
					image = cv2.resize(
						image,
						(self.resolution[1], self.resolution[0]),  # (width, height)
						interpolation=self.interpolation
					)

				image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

				images_tensor[i] = self.transform(image)

			images = images_tensor
			boxes = np.repeat(
				np.asarray([[0, 0, old_res[1], old_res[0]]], dtype=np.float32), len(images), axis=0
			)  # [x1, y1, x2, y2]
			heatmaps = np.zeros((len(images), self.nof_joints, self.resolution[0] // 4, self.resolution[1] // 4),
								dtype=np.float32)

		else:
			image_detections = self.detector.predict(images)

			base_index = 0
			nof_people = int(np.sum([len(d) for d in image_detections if d is not None]))
			boxes = np.empty((nof_people, 4), dtype=np.int32)
			images_tensor = torch.empty((nof_people, 3, self.resolution[0], self.resolution[1]))  # (height, width)
			heatmaps = np.zeros((nof_people, self.nof_joints, self.resolution[0] // 4, self.resolution[1] // 4),
								dtype=np.float32)

			for d, detections in enumerate(image_detections):
				image = images[d]
				if detections is not None and len(detections) > 0:
					for i, (x1, y1, x2, y2, conf, cls_conf, cls_pred) in enumerate(detections):
						x1 = int(round(x1.item()))
						x2 = int(round(x2.item()))
						y1 = int(round(y1.item()))
						y2 = int(round(y2.item()))

						# Adapt detections to match HRNet input aspect ratio (as suggested by xtyDoge in issue #14)
						correction_factor = self.resolution[0] / self.resolution[1] * (x2 - x1) / (y2 - y1)

						# TODO Use padding instead of bbox enlargement here too
						if correction_factor > 1:
							# increase y side
							center = y1 + (y2 - y1) // 2
							length = int(round((y2 - y1) * correction_factor))
							y1 = max(0, center - length // 2)
							y2 = min(image.shape[0], center + length // 2)
						elif correction_factor < 1:
							# increase x side
							center = x1 + (x2 - x1) // 2
							length = int(round((x2 - x1) * 1 / correction_factor))
							x1 = max(0, center - length // 2)
							x2 = min(image.shape[1], center + length // 2)

						boxes[base_index + i] = [x1, y1, x2, y2]
						images_tensor[base_index + i] = self.transform(image[y1:y2, x1:x2, ::-1])

					base_index += len(detections)

			images = images_tensor

		images = images.to(self.device)

		if images.shape[0] > 0:
			with torch.no_grad():
				if len(images) <= self.max_batch_size:
					out = self.model(images)

				else:
					out = torch.empty(
						(images.shape[0], self.nof_joints, self.resolution[0] // 4, self.resolution[1] // 4),
						device=self.device
					)
					for i in range(0, len(images), self.max_batch_size):
						out[i:i + self.max_batch_size] = self.model(images[i:i + self.max_batch_size])

			out = out.detach().cpu().numpy()
			pts = np.empty((out.shape[0], out.shape[1], 3), dtype=np.float32)
			# For each human, for each joint: y, x, confidence
			for i, human in enumerate(out):
				heatmaps[i] = human
				for j, joint in enumerate(human):
					pt = np.unravel_index(np.argmax(joint), (self.resolution[0] // 4, self.resolution[1] // 4))
					# 0: pt_y / (height // 4) * (bb_y2 - bb_y1) + bb_y1
					# 1: pt_x / (width // 4) * (bb_x2 - bb_x1) + bb_x1
					# 2: confidences
					pts[i, j, 0] = pt[0] * 1. / (self.resolution[0] // 4) * (boxes[i][3] - boxes[i][1]) + boxes[i][1]
					pts[i, j, 1] = pt[1] * 1. / (self.resolution[1] // 4) * (boxes[i][2] - boxes[i][0]) + boxes[i][0]
					pts[i, j, 2] = joint[pt]

			if self.multiperson:
				# re-add the removed batch axis (n)
				if self.return_heatmaps:
					heatmaps_batch = []
				if self.return_bounding_boxes:
					boxes_batch = []
				pts_batch = []
				index = 0
				for detections in image_detections:
					if detections is not None:
						pts_batch.append(pts[index:index + len(detections)])
						if self.return_heatmaps:
							heatmaps_batch.append(heatmaps[index:index + len(detections)])
						if self.return_bounding_boxes:
							boxes_batch.append(boxes[index:index + len(detections)])
						index += len(detections)
					else:
						pts_batch.append(np.zeros((0, self.nof_joints, 3), dtype=np.float32))
						if self.return_heatmaps:
							heatmaps_batch.append(np.zeros((0, self.nof_joints, self.resolution[0] // 4,
															self.resolution[1] // 4), dtype=np.float32))
						if self.return_bounding_boxes:
							boxes_batch.append(np.zeros((0, 4), dtype=np.float32))
				if self.return_heatmaps:
					heatmaps = heatmaps_batch
				if self.return_bounding_boxes:
					boxes = boxes_batch
				pts = pts_batch

			else:
				pts = np.expand_dims(pts, axis=1)

		else:
			boxes = np.asarray([], dtype=np.int32)
			if self.multiperson:
				pts = []
				for _ in range(len(image_detections)):
					pts.append(np.zeros((0, self.nof_joints, 3), dtype=np.float32))
			else:
				raise ValueError  # should never happen

		res = list()
		if self.return_heatmaps:
			res.append(heatmaps)
		if self.return_bounding_boxes:
			res.append(boxes)
		res.append(pts)

		if len(res) > 1:
			return res
		else:
			return res[0]
