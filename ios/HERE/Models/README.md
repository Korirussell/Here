# Object detection model (optional)

Drop a Core ML object-detection model whose file name starts with `YOLO` here (for example Apple's
`YOLOv3FP16.mlmodel`), then `xcodegen generate` and build. `ObjectScanner` picks it up automatically:
objects get tight boxes about 3 times a second, and the Apple region classifier keeps running at a
lower rate only for classes the detector lacks (doors, windows...).

Without a model here the app builds and runs the same, using the region classifier alone.

The model must output Vision `VNRecognizedObjectObservation`s (a detector with built-in
non-maximum suppression, as Apple's published YOLO models are) with COCO-style labels; the label
mapping is `ObjectClass.detectorLabels`.

## Getting the model used so far

Apple's published YOLOv3 (half-precision), 124 MB, 80 COCO classes, non-maximum suppression built in.
It is not checked in (too large for GitHub); fetch it once per checkout:

```bash
curl -L -o ios/HERE/Models/YOLOv3FP16.mlmodel \
  https://ml-assets.apple.com/coreml/models/Image/ObjectDetection/YOLOv3/YOLOv3FP16.mlmodel
cd ios && xcodegen generate
```

License: see https://github.com/pjreddie/darknet (per the model's own metadata).
