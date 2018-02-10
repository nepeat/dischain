import struct

HEADER_SIZE = 1 + 8
INITIAL_HEADER_SIZE = HEADER_SIZE + (8 * 3)


class Transaction:
    def __init__(self, server_id: int, channel_id: int, user_id: int, message_id: int, data: str):
        self.server_id = server_id
        self.channel_id = channel_id
        self.user_id = user_id
        self.message_id = message_id

        if isinstance(data, str):
            self.data = data.encode("utf8")
        else:
            self.data = data

    def chunk_count(self, size: int=80):
        return len(self.chunk_data(size))

    def chunk_data(self, size: int=80):
        data_copy = bytes(self.data)
        chunk_size = size - HEADER_SIZE
        chunks = []

        # Create the header chunk manually first.
        chunks.append(data_copy[0:size - INITIAL_HEADER_SIZE])
        data_copy = data_copy[size - INITIAL_HEADER_SIZE:]

        # Create the rest of the chunks automatically
        for i in range(0, len(data_copy), chunk_size):
            chunks.append(data_copy[i:i + chunk_size])

        return chunks

    def generate_return_payloads(self, size: int=80):
        nonce = 0
        chunks = self.chunk_data(size)

        # Sanity check
        if len(chunks) > 255:
            raise Exception(f"Chunk count exceeds nonce limit. ({len(chunks) > 255})")

        for chunk in chunks:
            chunk_len = len(chunk)

            if nonce == 0:
                payload = struct.pack(f"!BQQQQ{chunk_len}s", nonce, self.message_id, self.server_id, self.channel_id, self.user_id, chunk)
            else:
                payload = struct.pack(f"!BQ{chunk_len}s", nonce, self.message_id, chunk)

            yield payload

            nonce += 1