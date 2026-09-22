import numpy as np

def load_audio(audio_path, sample_rate=16000, use_normalizer=True, start_second=None, duration=None):
    """Load 16 kHz PCM, optionally decoding only a requested segment."""
    from pydub import AudioSegment
    
    # Use pydub start_second and duration options to limit decoding when supported.
    # Forward these through kwargs; behavior depends on the installed pydub version.
    load_kwargs = {
        "frame_rate": sample_rate, 
        "channels": 1
    }
    if start_second: load_kwargs['start_second'] = start_second
    if duration: load_kwargs['duration'] = duration

    audio_segment = AudioSegment.from_file(audio_path, **load_kwargs)

    bit_depth = audio_segment.sample_width * 8
    max_val = float(1 << (bit_depth - 1))
    
    audio = np.array(
        audio_segment
        .set_channels(1)
        .set_frame_rate(sample_rate)
        .get_array_of_samples(),
    ) / max_val

    
    
    return audio

