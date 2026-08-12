import asyncio

from core.client.transcribe.file_transcriber import read_fixed_chunk


class FragmentedReader:
    """模拟 FFmpeg 管道每次只交付少量可用字节。"""

    def __init__(self, fragments: list[bytes]):
        self.fragments = [bytearray(fragment) for fragment in fragments]

    async def read(self, size: int) -> bytes:
        while self.fragments and not self.fragments[0]:
            self.fragments.pop(0)
        if not self.fragments:
            return b""

        fragment = self.fragments[0]
        result = bytes(fragment[:size])
        del fragment[:size]
        return result


def test_read_fixed_chunk_accumulates_fragmented_pipe_reads():
    async def run():
        reader = FragmentedReader([b"ab", b"c", b"de", b"f"])
        assert await read_fixed_chunk(reader, 5) == b"abcde"
        assert await read_fixed_chunk(reader, 5) == b"f"
        assert await read_fixed_chunk(reader, 5) == b""

    asyncio.run(run())


def test_read_fixed_chunk_rejects_invalid_size():
    async def run():
        reader = FragmentedReader([])
        try:
            await read_fixed_chunk(reader, 0)
        except ValueError as exc:
            assert "必须为正数" in str(exc)
        else:
            raise AssertionError("无效分块大小应当被拒绝")

    asyncio.run(run())
