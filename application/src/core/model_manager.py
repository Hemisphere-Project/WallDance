"""
Model management for WallDance.
Handles YOLO model loading, downloading, and TensorRT export.
"""

import os
import shutil
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from ultralytics import YOLO

# Check if TensorRT is available
_TENSORRT_AVAILABLE = False
try:
    import tensorrt
    _TENSORRT_AVAILABLE = True
except ImportError:
    pass


class ModelStatus(Enum):
    """Status of model preparation."""
    IDLE = "idle"
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    EXPORTING_TENSORRT = "exporting_tensorrt"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


@dataclass
class ModelProgress:
    """Progress information for model operations."""
    status: ModelStatus = ModelStatus.IDLE
    message: str = ""
    progress: float = 0.0  # 0.0 to 1.0
    error: Optional[str] = None


def is_tensorrt_available() -> bool:
    """Check if TensorRT is available on this system."""
    return _TENSORRT_AVAILABLE


class ModelManager:
    """
    Manages YOLO model lifecycle:
    - Check if .pt model exists (download if missing via ultralytics)
    - Check if .engine exists (export if missing)
    - Load the appropriate model
    """

    def __init__(self, models_dir: str, use_tensorrt: bool = True, imgsz: int = 640):
        """
        Args:
            models_dir: Directory where models are stored
            use_tensorrt: If True, prefer TensorRT .engine files
            imgsz: Input image size for TensorRT engine (default 640)
        """
        self.models_dir = models_dir
        self.use_tensorrt = use_tensorrt
        self.imgsz = imgsz  # TensorRT engines are size-specific
        self.progress = ModelProgress()
        self._progress_callback: Optional[Callable[[ModelProgress], None]] = None
        self._using_tensorrt = False  # Track if we're using TensorRT engine
        self._tensorrt_fallback_reason: Optional[str] = None  # Why TensorRT wasn't used

    def set_imgsz(self, imgsz: int):
        """Set the image size for TensorRT engines."""
        self.imgsz = imgsz

    def set_progress_callback(self, callback: Callable[[ModelProgress], None]):
        """Set callback to receive progress updates."""
        self._progress_callback = callback

    def is_using_tensorrt(self) -> bool:
        """Check if currently loaded model is TensorRT engine."""
        return self._using_tensorrt
    
    def get_tensorrt_fallback_reason(self) -> Optional[str]:
        """Get reason why TensorRT wasn't used (None if TensorRT is being used)."""
        return self._tensorrt_fallback_reason

    def _update_progress(self, status: ModelStatus, message: str, progress: float = 0.0, error: Optional[str] = None):
        """Update progress and notify callback."""
        self.progress = ModelProgress(status=status, message=message, progress=progress, error=error)
        if self._progress_callback:
            self._progress_callback(self.progress)
        print(f"[ModelManager] {status.value}: {message} ({progress*100:.0f}%)")

    def get_pt_path(self, model_name: str) -> str:
        """Get path to .pt model file."""
        if not model_name.endswith('.pt'):
            model_name = f"{model_name}.pt"
        return os.path.join(self.models_dir, model_name)

    def get_engine_path(self, model_name: str, imgsz: Optional[int] = None) -> str:
        """Get path to .engine TensorRT file.
        
        Args:
            model_name: Model name
            imgsz: Image size (uses self.imgsz if not specified)
        """
        base_name = model_name.replace('.pt', '').replace('.engine', '')
        # Remove any existing size suffix (e.g., _640)
        if '_' in base_name and base_name.split('_')[-1].isdigit():
            base_name = '_'.join(base_name.split('_')[:-1])
        sz = imgsz or self.imgsz
        return os.path.join(self.models_dir, f"{base_name}_{sz}.engine")

    def model_exists(self, model_name: str) -> bool:
        """Check if .pt model exists."""
        return os.path.exists(self.get_pt_path(model_name))

    def engine_exists(self, model_name: str, imgsz: Optional[int] = None) -> bool:
        """Check if .engine file exists for current imgsz."""
        return os.path.exists(self.get_engine_path(model_name, imgsz))

    def load_model(self, model_name: str, force_pt: bool = False) -> YOLO:
        """
        Load a YOLO model, with TensorRT export if needed.
        
        Args:
            model_name: Model name (e.g., "yolo11m-pose" or "yolo11m-pose.pt")
            force_pt: If True, skip TensorRT and use .pt directly
            
        Returns:
            Loaded YOLO model
            
        Raises:
            Exception if loading fails
        """
        os.makedirs(self.models_dir, exist_ok=True)
        
        # Normalize model name
        base_name = model_name.replace('.pt', '').replace('.engine', '')
        pt_path = self.get_pt_path(base_name)
        engine_path = self.get_engine_path(base_name)

        # Step 1: Check/download .pt model
        self._update_progress(ModelStatus.CHECKING, f"Checking {base_name}.pt...", 0.1)
        
        if not os.path.exists(pt_path):
            self._update_progress(ModelStatus.DOWNLOADING, f"Downloading {base_name}.pt...", 0.2)
            try:
                # YOLO will auto-download if model doesn't exist
                model = YOLO(f"{base_name}.pt")
                # Move to our models directory if downloaded elsewhere
                # (Ultralytics usually downloads to current dir or ~/.cache)
                self._update_progress(ModelStatus.DOWNLOADING, f"Downloaded {base_name}.pt", 0.4)
            except Exception as e:
                self._update_progress(ModelStatus.ERROR, f"Failed to download {base_name}.pt", 0.0, str(e))
                raise

        # Step 2: Check/export TensorRT engine
        use_engine = False
        if self.use_tensorrt and not force_pt and is_tensorrt_available():
            if os.path.exists(engine_path):
                # Engine already exists for current imgsz
                use_engine = True
            else:
                # Need to export with current imgsz
                self._update_progress(
                    ModelStatus.EXPORTING_TENSORRT,
                    f"Exporting TensorRT engine for {self.imgsz}x{self.imgsz} (this may take 2-5 minutes)...",
                    0.5
                )
                try:
                    # Load .pt model first
                    pt_model = YOLO(pt_path)
                    
                    # Export to TensorRT with FP16 and current imgsz
                    # This blocks and takes 2-5 minutes
                    self._update_progress(
                        ModelStatus.EXPORTING_TENSORRT,
                        f"Building TensorRT engine for {base_name} @ {self.imgsz}x{self.imgsz}...",
                        0.6
                    )
                    
                    # Export - this is the slow part
                    # Use the current imgsz setting
                    export_path = pt_model.export(
                        format="engine",
                        imgsz=self.imgsz,  # Use current imgsz setting
                        half=True,  # FP16 for speed
                        device=0,
                        verbose=False,
                    )
                    
                    # Move/rename to models dir with imgsz suffix
                    if export_path and os.path.exists(export_path):
                        # Rename to include imgsz (e.g., yolo11n-pose_960.engine)
                        target = engine_path  # Already includes imgsz from get_engine_path()
                        if os.path.abspath(export_path) != os.path.abspath(target):
                            shutil.move(export_path, target)
                        engine_path = target
                        use_engine = True
                    
                    self._update_progress(
                        ModelStatus.EXPORTING_TENSORRT,
                        f"TensorRT engine created: {os.path.basename(engine_path)}",
                        0.9
                    )
                    
                except Exception as e:
                    self._update_progress(
                        ModelStatus.LOADING,
                        f"TensorRT export failed, using PyTorch",
                        0.5,
                    )
                    print(f"TensorRT export failed: {e}")
                    print("Falling back to PyTorch model...")
                    self._tensorrt_fallback_reason = f"TensorRT export failed: {str(e)[:50]}"
                    use_engine = False
        elif self.use_tensorrt and not force_pt and not is_tensorrt_available():
            print("TensorRT not available on this system, using PyTorch model")
            self._update_progress(
                ModelStatus.LOADING,
                "TensorRT not available, using PyTorch",
                0.5
            )
            self._tensorrt_fallback_reason = "TensorRT not installed on this system"

        # Step 3: Load the model
        if use_engine and os.path.exists(engine_path):
            self._update_progress(ModelStatus.LOADING, f"Loading TensorRT engine...", 0.95)
            try:
                model = YOLO(engine_path)
                # Validate the engine with a test inference — incompatible engines
                # (wrong TRT version) load without error but fail at inference time.
                import numpy as np
                dummy = np.zeros((64, 64, 3), dtype=np.uint8)
                _ = model(dummy, verbose=False)
                self._using_tensorrt = True  # Track that we're using TensorRT
                self._tensorrt_fallback_reason = None  # Clear - TensorRT worked
                self._update_progress(ModelStatus.READY, f"TensorRT model ready: {base_name}", 1.0)
                return model
            except Exception as e:
                print(f"Failed to load TensorRT engine: {e}")
                print("Falling back to PyTorch model...")
                self._tensorrt_fallback_reason = f"TensorRT engine incompatible or failed: {str(e)[:60]}"

        # Load .pt model (fallback or if TensorRT disabled/unavailable)
        self._update_progress(ModelStatus.LOADING, f"Loading PyTorch model...", 0.95)
        try:
            model = YOLO(pt_path)
            self._using_tensorrt = False  # Track that we're using PyTorch
            self._update_progress(ModelStatus.READY, f"PyTorch model ready: {base_name}", 1.0)
            return model
        except Exception as e:
            self._update_progress(ModelStatus.ERROR, f"Failed to load model", 0.0, str(e))
            raise

    def get_available_models(self) -> list:
        """Get list of available model base names in models directory."""
        models = set()
        if os.path.exists(self.models_dir):
            for f in os.listdir(self.models_dir):
                if f.endswith('.pt') or f.endswith('.engine'):
                    base = f.replace('.pt', '').replace('.engine', '')
                    if 'pose' in base:  # Only pose models
                        models.add(base)
        return sorted(models)

    def get_model_info(self, model_name: str) -> dict:
        """Get info about a model (what files exist, etc.)."""
        base_name = model_name.replace('.pt', '').replace('.engine', '')
        pt_path = self.get_pt_path(base_name)
        engine_path = self.get_engine_path(base_name)
        
        info = {
            'name': base_name,
            'pt_exists': os.path.exists(pt_path),
            'engine_exists': os.path.exists(engine_path),
            'pt_size_mb': 0,
            'engine_size_mb': 0,
        }
        
        if info['pt_exists']:
            info['pt_size_mb'] = os.path.getsize(pt_path) / (1024 * 1024)
        if info['engine_exists']:
            info['engine_size_mb'] = os.path.getsize(engine_path) / (1024 * 1024)
            
        return info
