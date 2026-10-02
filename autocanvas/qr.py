"""Independent QR analysis of timestamped images; no workflow or course dependencies."""
import hashlib
from pathlib import Path


class QRDetector:
    def __init__(self):
        import cv2
        self.cv2 = cv2
        self.detector = cv2.QRCodeDetector()

    def _decode_candidate(self, image, corners):
        """Rectify and retry a candidate. A quadrilateral alone is not QR evidence."""
        import numpy as np
        cv2 = self.cv2
        points = np.asarray(corners, dtype=np.float32).reshape(4, 2)
        if not np.isfinite(points).all():
            return ''
        side = max(np.linalg.norm(points[i]-points[(i+1) % 4]) for i in range(4))
        if side < 20 or abs(cv2.contourArea(points)) < 400:
            return ''
        size = int(min(1024, max(256, side*2)))
        target = np.float32([[0,0],[size-1,0],[size-1,size-1],[0,size-1]])
        transform = cv2.getPerspectiveTransform(points, target)
        crop = cv2.warpPerspective(image, transform, (size,size), flags=cv2.INTER_CUBIC,
                                   borderMode=cv2.BORDER_CONSTANT, borderValue=(255,255,255))
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
        for variant in (gray, binary):
            padded = cv2.copyMakeBorder(variant, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255)
            text, _, _ = self.detector.detectAndDecode(padded)
            if text:
                return text
        return ''

    def detect(self, path):
        import numpy as np
        cv2 = self.cv2
        data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
        if image is None:
            raise ValueError('Unreadable image')
        return self.detect_array(image)

    def detect_bytes(self, data):
        import numpy as np
        image = self.cv2.imdecode(np.frombuffer(data, dtype=np.uint8), self.cv2.IMREAD_COLOR)
        if image is None:
            return []
        return self.detect_array(image)

    def detect_array(self, image):
        # OpenCV returns false candidate boxes on ordinary text and diagrams.
        # Only a successful payload decode confirms an event.
        _, texts, points, _ = self.detector.detectAndDecodeMulti(image)
        if points is None:
            text, point, _ = self.detector.detectAndDecode(image)
            texts, points = [text], point
        if points is None:
            return []
        rows = []
        for index, corners in enumerate(points):
            text = texts[index] if index < len(texts) else ''
            if not text:
                text = self._decode_candidate(image, corners)
            if text:
                rows.append({'content': text, 'decoded': True, 'corners': corners.tolist()})
        return rows


def scan_images(folder: Path, frames, *, detector=None):
    """Return observations, not attendance decisions. Input images must be local basenames."""
    detector = detector or QRDetector()
    events, errors = [], []
    for frame in frames:
        name = str(frame['image'])
        path = folder/name
        if Path(name).name != name or not path.resolve().is_relative_to(folder.resolve()):
            errors.append({'image': name, 'error': 'UnsafeImagePath'})
            continue
        try:
            detections = detector.detect(path)
        except Exception as error:
            errors.append({'image': name, 'error': type(error).__name__})
            continue
        for index, detection in enumerate(detections):
            start = float(frame.get('time_seconds', 0))
            identity = f'{name}:{index}:{detection["content"]}'
            events.append({
                'id': hashlib.sha256(identity.encode()).hexdigest()[:24],
                'start': start, 'image': name, **detection,
            })
    return {'version': 2, 'confirmation': 'decoded_payload', 'status': 'partial' if errors else 'complete',
            'coverage': 'saved_slides', 'scanned_images': len(frames)-len(errors),
            'total_images': len(frames), 'events': events, 'errors': errors}
