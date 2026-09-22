import os
from pathlib import Path

# Git-tracked server configuration defaults.
# Copy this file to the repository root as config_server.py for first use.
# Keep local settings in that copy. Do not move or edit this template for local use.

# Configuration version.
__version__ = '2.6'

# Application directory when copied to the repository root.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# Server configuration.
# format_num/format_spell reload for new tasks; model/network/resource changes
# require restart. See docs/reference/configuration.md.
class ServerConfig:
    # Fallback UI language when the local ClientConfig has no ui_language field.
    # Normally follows config_client.py, including live language-menu changes.
    # Values: 'auto' (system), 'en', or 'zh-CN'. Independent of ASR language.
    ui_language = 'auto'

    # Network mode: 'local' restricts access to loopback; 'lan' requires token authentication.
    network_mode = 'local'
    addr = '127.0.0.1'
    port = '6016'

    # Read the LAN token from the environment; clients must use the same token.
    # Generate a token with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
    auth_token = os.environ.get('CAPSWRITER_AUTH_TOKEN', '')

    # Optional TLS: configure certificate and key, and enable client TLS on untrusted networks.
    tls_certfile = ''
    tls_keyfile = ''

    # Input and resource limits accommodate roughly one minute of float32 audio per message.
    websocket_max_message_bytes = 6 * 1024 * 1024
    websocket_max_queue = 16
    max_connections = 8
    connection_idle_timeout = 300

    # Bound per-task audio and inference queues to limit memory and compute use.
    max_message_audio_bytes = 4 * 1024 * 1024
    max_task_audio_bytes = 4 * 60 * 60 * 16000 * 4  # At most four hours of audio.
    max_task_duration = 6 * 60 * 60                  # Keep a task for at most six hours.
    max_context_length = 4096
    max_tasks_per_connection = 4
    queue_in_maxsize = 32
    queue_out_maxsize = 32
    align_queue_in_maxsize = 4
    align_queue_out_maxsize = 4
    worker_buffer_max_tasks = 64

    # Bound each result send; a stalled client is disconnected without retrying.
    result_send_timeout = 10.0
    # Maximum IPC read stall / worker output-capacity wait before stopping service.
    result_queue_timeout = 60.0
    # Stop the service if model startup stalls. Slow machines may need longer limits.
    model_startup_timeout = 300.0
    # Maximum worker-loop stall or task inactivity (including queued partial tasks).
    worker_stall_timeout = 600.0

    # ASR engine: 'qwen_asr', 'fun_asr_nano', 'sensevoice', or 'paraformer'.
    model_type = 'qwen_asr'

    format_num = True       # Convert Chinese number words to Arabic numerals in output.
    format_spell = True     # Adjust spacing between Chinese and English text.

    enable_tray = True        # Enable the tray icon.

    # Logging settings.
    log_level = 'DEBUG'        # Log level: 'DEBUG', 'INFO', 'WARNING', 'ERROR', or 'CRITICAL'.
    # Load the forced aligner on demand in a sibling process; exit it when idle instead
    # of unloading shared GPU backends inside ASR. Use 0 to keep the process resident.
    aligner_idle_timeout = 1   # Release GPU memory promptly; the supervisor replaces the idle process.
    aligner_request_timeout = 60  # Request deadline; stop service on timeout to reap a wedged aligner.

    # Raise GPU memory clocks before recognition to reduce latency; requires administrator rights.
    gpu_boost_enabled = False                   # Master switch; disabled by default.
    gpu_boost_cmd = 'nvidia-smi -lmc 9000'      # Lock GPU memory to 9000 MHz; adjust for the installed GPU.
    gpu_unboost_cmd = 'nvidia-smi -rmc'         # Restore default GPU memory clocks.
    gpu_unboost_timeout = 1                     # Idle seconds before restoring default clocks.

    # Sample NVIDIA dedicated memory during inference for advisory warnings only.
    # Disable silently when nvidia-smi is unavailable, including AMD/Intel systems.
    gpu_memory_warning_enabled = True
    gpu_memory_warning_interval = 1.0           # Sampling interval in seconds; minimum 0.5.
    gpu_memory_warning_threshold = 0.90         # Dedicated memory usage threshold.
    gpu_memory_warning_consecutive_samples = 3  # Require consecutive samples to ignore transient peaks.

    # Integrated GPU compatibility workarounds.
    # os.environ["GGML_VK_DISABLE_COOPMAT"] = "1"   # Try if AMD integrated GPUs cannot load GGUF.
    # os.environ["GGML_VK_DISABLE_F16"] = "1"       # Try for decode errors or forced circuit breaks.




class ModelDownloadLinks:
    """Model download locations."""
    # Use the GitHub model release page for all downloads.
    models_page = "https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models"


class ModelPaths:
    """Model file paths."""

    # Base directory.
    model_dir = Path() / 'models'

    # Paraformer model paths.
    paraformer_dir = model_dir / 'Paraformer' / "speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx"
    paraformer_model = paraformer_dir / 'model.onnx'
    paraformer_tokens = paraformer_dir / 'tokens.txt'

    # Punctuation model path.
    punc_model_dir = model_dir / 'Punct-CT-Transformer' / 'sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12' / 'model.onnx'

    # SenseVoice includes punctuation.
    sensevoice_dir = model_dir / 'SenseVoice-Small' / 'Sensevoice-Small-ONNX'
    sensevoice_encoder = sensevoice_dir / 'SenseVoice-Encoder.fp16.onnx'
    sensevoice_decoder = sensevoice_dir / 'SenseVoice-CTC.fp16.onnx'
    sensevoice_tokenizer = sensevoice_dir / 'tokenizer.bpe.model'


    # Fun-ASR-Nano includes punctuation.
    fun_asr_nano_gguf_dir = model_dir / 'Fun-ASR-Nano' / 'Fun-ASR-Nano-GGUF'
    fun_asr_nano_gguf_encoder_adaptor = fun_asr_nano_gguf_dir / 'Fun-ASR-Nano-Encoder-Adaptor.fp16.onnx'
    fun_asr_nano_gguf_ctc = fun_asr_nano_gguf_dir / 'Fun-ASR-Nano-CTC.fp16.onnx'
    fun_asr_nano_gguf_llm_decode = fun_asr_nano_gguf_dir / 'Fun-ASR-Nano-Decoder.q5_k.gguf'
    fun_asr_nano_gguf_token = fun_asr_nano_gguf_dir / 'tokens.txt'

    # Qwen3-ASR includes punctuation.
    qwen3_asr_gguf_dir = model_dir / 'Qwen3-ASR' / 'Qwen3-ASR-1.7B'
    qwen3_asr_gguf_encoder_frontend = qwen3_asr_gguf_dir / 'qwen3_asr_encoder_frontend.onnx'
    qwen3_asr_gguf_encoder_backend = qwen3_asr_gguf_dir / 'qwen3_asr_encoder_backend.onnx'
    qwen3_asr_gguf_llm_decode = qwen3_asr_gguf_dir / 'qwen3_asr_llm.gguf'

    # Forced aligner model paths.
    force_aligner_gguf_dir = model_dir / 'Qwen3-ForcedAligner' / 'Qwen3-ForcedAligner-0.6B'
    force_aligner_gguf_encoder_frontend = force_aligner_gguf_dir / 'qwen3_aligner_encoder_frontend.int4.onnx'
    force_aligner_gguf_encoder_backend = force_aligner_gguf_dir / 'qwen3_aligner_encoder_backend.int4.onnx'
    force_aligner_gguf_llm_decode = force_aligner_gguf_dir / 'qwen3_aligner_llm.q5_k.gguf'



class ParaformerArgs:
    """Paraformer model arguments."""

    paraformer = ModelPaths.paraformer_model.as_posix()
    tokens = ModelPaths.paraformer_tokens.as_posix()
    num_threads = 4
    sample_rate = 16000
    feature_dim = 80
    decoding_method = 'greedy_search'
    provider = 'cpu'
    debug = False


class SenseVoiceArgs:
    """SenseVoice model arguments."""

    encoder_path = ModelPaths.sensevoice_encoder.as_posix()
    decoder_path = ModelPaths.sensevoice_decoder.as_posix()
    tokenizer_path = ModelPaths.sensevoice_tokenizer.as_posix()
    itn = True                  # Produce Arabic numerals natively.
    onnx_provider = 'CPU'       # ONNX execution provider: CPU or DML.
    dml_pad_to = 30             # Pad short audio to this duration for DirectML execution.


class FunASRNanoGGUFArgs:
    """Fun-ASR-Nano GGUF model arguments."""

    # Model paths.
    encoder_onnx_path = ModelPaths.fun_asr_nano_gguf_encoder_adaptor.as_posix()
    ctc_onnx_path = ModelPaths.fun_asr_nano_gguf_ctc.as_posix()
    decoder_gguf_path = ModelPaths.fun_asr_nano_gguf_llm_decode.as_posix()
    tokens_path = ModelPaths.fun_asr_nano_gguf_token.as_posix()

    # GPU acceleration.
    onnx_provider = 'CPU'       # ONNX execution provider: CPU or DML.
    llm_use_gpu = True          # Enable GPU acceleration for GGUF.
    vulkan_force_fp32 = False   # Force FP32; try for precision overflow on Intel integrated GPUs.
    
    # Model parameters.
    enable_ctc = True           # Enable CTC timestamp alignment.
    n_predict = 512             # Maximum generated tokens.
    n_threads = None            # Thread count; None selects automatically.
    dml_pad_to = 30             # Pad short audio to this duration for DirectML execution.
    verbose = False

class Qwen3ASRGGUFArgs:
    """Qwen3-ASR GGUF model arguments."""

    # Model paths.
    model_dir = ModelPaths.qwen3_asr_gguf_dir.as_posix()
    encoder_frontend_fn = ModelPaths.qwen3_asr_gguf_encoder_frontend.name
    encoder_backend_fn = ModelPaths.qwen3_asr_gguf_encoder_backend.name
    llm_fn = ModelPaths.qwen3_asr_gguf_llm_decode.name

    # GPU acceleration.
    onnx_provider = 'DML'       # Use DirectML for the ONNX encoder; fall back to CPU if unavailable.
    llm_use_gpu = True          # Enable GPU acceleration for GGUF.
    
    # Model parameters.
    n_ctx = 2048                # Context window size.
    chunk_size = 80.0           # Segment duration in seconds.
    memory_num = 1              # Number of remembered segments.
    dml_pad_to = 30             # Pad short audio to this duration for DirectML execution.
    verbose = False


class ForceAlignerGGUFArgs:
    """Forced aligner GGUF model arguments."""

    # Model paths.
    model_dir = ModelPaths.force_aligner_gguf_dir.as_posix()
    encoder_frontend_fn = ModelPaths.force_aligner_gguf_encoder_frontend.name
    encoder_backend_fn = ModelPaths.force_aligner_gguf_encoder_backend.name
    llm_fn = ModelPaths.force_aligner_gguf_llm_decode.name

    # GPU acceleration.
    onnx_provider = 'DML'       # Use DirectML for the ONNX encoder; fall back to CPU if unavailable.
    llm_use_gpu = True          # Offload GGUF decoder layers to the GPU.
    
    # Alignment parameters.
    n_ctx = 3072                # Context window size.
    dml_pad_to = 30             # Pad short audio to this duration for DirectML execution.
