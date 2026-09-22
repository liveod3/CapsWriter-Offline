
from core.i18n import tr
from pathlib import Path
import numpy as np
import onnxruntime as ort

class SenseVoiceDecoder:
    def __init__(self, decoder_path: str, onnx_provider="cpu", dml_pad_to: int = 30):
        # 1. Resource paths.
        self.model_path = decoder_path
        decoder_path = Path(decoder_path)
        
        self.onnx_provider = onnx_provider.upper()

        # 2. Initialize the session.
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
        
        self.session = ort.InferenceSession(str(decoder_path), providers=providers, sess_options=session_opts)

        # 3. Adapt precision.
        in_type = self.session.get_inputs()[0].type
        self.input_dtype = np.float16 if 'float16' in in_type else np.float32

        # 4. Warm up DirectML.
        self.use_dml = (self.onnx_provider == "DML")
        self.fixed_len = int(dml_pad_to * 17) + 4 # One second is roughly 17 frames plus four prompt frames.
        if self.use_dml and isinstance(dml_pad_to, int) and dml_pad_to > 0:
            self.warmup()

    def warmup(self):
        """Run the full padded shape to specialize CTC head operators."""
        # CTC input is typically (1, T_plus_4, 512).
        dummy_enc = np.zeros((1, self.fixed_len, 512), dtype=self.input_dtype)
        print(tr('terminal.decoder.decoder_dml_warmup_with_data_shape', value0=dummy_enc.shape))
        self.session.run(None, {"enc_out": dummy_enc})
        print(tr('terminal.decoder.decoder_dml_warmup_complete'))

    def forward(self, enc_out):
        """
        Run the CTC head once.
        """
        if enc_out.dtype != self.input_dtype:
            enc_out = enc_out.astype(self.input_dtype)
            
        # The model returns top-100 probabilities and indexes in one call.
        topk_log_probs, topk_indices = self.session.run(None, {"enc_out": enc_out})
        return topk_log_probs, topk_indices

    def decode(self, enc_out, sp, prompt_len=4, T_valid=None, blank_id=0):
        """
        Run one inference call for all decoding data.
        Return text and timestamps after removing blanks and adjacent duplicates.
        """
        # 1. Make the single inference call.
        _, topk_indices = self.forward(enc_out)
        
        # Select the valid region, skipping prompt frames.
        start = prompt_len
        end = (T_valid + prompt_len) if T_valid is not None else topk_indices.shape[1]
        
        # The model returns top-k; use top-1 without expanding candidate probabilities.
        top1_indices = topk_indices[0, start:end, 0]
        
        # Build greedy results from top-1 indexes.
        greedy_ids = top1_indices
        collapsed = []
        if len(greedy_ids) > 0:
            curr_id = greedy_ids[0]
            start_frame = 0
            for i in range(1, len(greedy_ids)):
                if greedy_ids[i] != curr_id:
                    collapsed.append((curr_id, start_frame))
                    curr_id = greedy_ids[i]
                    start_frame = i
            collapsed.append((curr_id, start_frame))

        greedy_results = []
        for tid, fidx in collapsed:
            if tid == blank_id: continue
            char = sp.id_to_piece(int(tid)).replace("\u2581", " ")
            if not char.strip() and char != " ": continue
            else:
                greedy_results.append({
                    "text": char,
                    "start": round(fidx * 0.060, 3)
                })

        return greedy_results
