
from core.i18n import tr
from pathlib import Path
import json
import numpy as np
import onnxruntime as ort

class SenseVoiceEncoder:
    def __init__(self, encoder_path: str, onnx_provider="cpu", dml_pad_to: int = 30):
        # 1. Resource paths.
        self.model_path = encoder_path # Retain the model path for the TensorRT cache.
        encoder_path = Path(encoder_path)
        
        self.onnx_provider = onnx_provider.upper()

        # 2. Initialize the session through provider selection.
        
        # 3. Initialize the session through provider selection.
        available_providers = ort.get_available_providers()
        providers = ['CPUExecutionProvider']
        
        if self.onnx_provider in ('TRT', 'TENSORRT') and 'TensorrtExecutionProvider' in available_providers:
            providers.insert(0, ('TensorrtExecutionProvider', {
                'trt_fp16_enable': True,
                'trt_engine_cache_enable': True,
                'trt_engine_cache_path': Path(self.model_path).parent / 'trt_cache',
            }))
        elif self.onnx_provider == 'DML' and 'DmlExecutionProvider' in available_providers:
            providers.insert(0, 'DmlExecutionProvider')
        elif self.onnx_provider == 'CUDA' and 'CUDAExecutionProvider' in available_providers:
            providers.insert(0, 'CUDAExecutionProvider')
        
        session_opts = ort.SessionOptions()
        session_opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        session_opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
        session_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        
        self.session = ort.InferenceSession(str(encoder_path), providers=providers, sess_options=session_opts)
        
        # 3. Load embedded configuration from metadata.
        meta = self.session.get_modelmeta().custom_metadata_map
        if "lid_dict" in meta:
            self.config = {
                "lid_dict": json.loads(meta["lid_dict"]),
                "textnorm_dict": json.loads(meta["textnorm_dict"]),
                "emo_dict": json.loads(meta["emo_dict"]),
                "input_size": int(meta.get("input_size", 560)),
                "output_size": int(meta.get("output_size", 512))
            }
        else:
            print(tr('terminal.encoder.encoder_warning_onnx_metadata_missing_using_built_in'))
            self.config = {} # Otherwise use defaults.

        # 4. Detect FP32 or FP16 precision.
        in_type = self.session.get_inputs()[0].type
        self.input_dtype = np.float16 if 'float16' in in_type else np.float32

        # 5. Apply DirectML-specific settings.
        self.use_dml = (self.onnx_provider.lower() == "dml")
        self.fixed_len = int(dml_pad_to * 17) # One second is roughly 17 LFR frames.
        if self.use_dml and isinstance(dml_pad_to, int) and dml_pad_to > 0:
            self.warmup()

    def warmup(self):
        """Run the full padded shape to specialize DirectML operators."""
        dummy_lfr = np.random.randn(1, self.fixed_len, 560).astype(self.input_dtype)
        dummy_mask = np.ones((1, self.fixed_len), dtype=self.input_dtype)
        dummy_prompt = np.zeros((1, 4, 560), dtype=self.input_dtype)
        print(tr('terminal.encoder.encoder_dml_warmup_with_s_random_data_of', value0=dummy_lfr.shape, value1=self.fixed_len // 17))
        self.session.run(None, {
            "speech_feat": dummy_lfr,
            "mask": dummy_mask,
            "prompt_ids": np.zeros((1, 4), dtype=np.int64)
        })
        print(tr('terminal.encoder.encoder_dml_warmup_complete'))

    def construct_prompt(self, lid="auto", itn=True):
        """Construct four prompt token IDs."""
        lid_dict = self.config.get("lid_dict", {})
        itn_dict = self.config.get("textnorm_dict", {})
        
        lid_idx = lid_dict.get(lid, 0) 
        itn_str = "withitn" if itn else "woitn"
        itn_idx = itn_dict.get(itn_str, 14)
        
        # Model order: one language token, two event/emotion tokens, one style token.
        # Return indexes only; ONNX performs Gather internally.
        prompt_ids = np.array([lid_idx, 1, 2, itn_idx], dtype=np.int64)
        return prompt_ids[np.newaxis, :] # (1, 4)

    def forward(self, lfr_feat, lid="zh", itn=True):
        """
        Run encoder inference.
        Return enc_out shaped (1, T+4, 512).
        """
        # 1. Build prompt IDs.
        prompt_ids = self.construct_prompt(lid=lid, itn=itn)
        
        T_valid = lfr_feat.shape[0]
        
        if self.use_dml and T_valid < self.fixed_len:
            # DirectML uses fixed-length padding with repeated final frames.
            T_target = self.fixed_len
            
            # Build the mask: 1 for valid frames, 0 for padding.
            mask = np.zeros((1, T_target), dtype=self.input_dtype)
            mask[0, :T_valid] = 1.0
            
            # Pad features by repeating the final frame.
            full_feat = np.empty((1, T_target, 560), dtype=self.input_dtype)
            full_feat[0, :T_valid, :] = lfr_feat.astype(self.input_dtype)
            full_feat[0, T_valid:, :] = lfr_feat[-1, :].astype(self.input_dtype) # Replicate
            
            # 3. Run inference.
            enc_out = self.session.run(None, {
                "speech_feat": full_feat,
                "mask": mask,
                "prompt_ids": prompt_ids
            })[0]
            
            # 4. Return the full padded output.
            # Internal masks zero padded outputs; retaining them keeps decoder shapes stable.
            return enc_out
        else:
            # Use dynamic axes outside DML or beyond the fixed length.
            mask = np.ones((1, T_valid), dtype=self.input_dtype)
            enc_out = self.session.run(None, {
                "speech_feat": lfr_feat[np.newaxis, ...].astype(self.input_dtype),
                "mask": mask,
                "prompt_ids": prompt_ids
            })[0]
            return enc_out
