import numpy as np

def _encode_int(item:int):
    size = item.bit_length() // 8 + 1
    signed = item < 0
    bytes_data = item.to_bytes(size, 'little', signed=signed)
    return ('i', size, signed), memoryview(bytes_data)

def _decode_int(byte_data:memoryview, info:tuple):
    return int.from_bytes(byte_data, 'little', signed=info[2])

def _encode_float(item:float):
    byte_data = np.float64(item).tobytes()
    return ('f', 8), memoryview(byte_data)

def _decode_float(byte_data:memoryview, info:tuple):
    return np.frombuffer(byte_data, dtype=np.float64)[0]

def _encode_bool(item:bool):
    byte_data = item.to_bytes(1, 'little')
    return ('b', 1), memoryview(byte_data)

def _decode_bool(byte_data:memoryview, info:tuple):
    return bool.from_bytes(byte_data, 'little')

def _encode_bytes(item:bytes):
    return ('r', len(item)), memoryview(item)

def _decode_bytes(byte_data:memoryview, info:tuple):
    if byte_data is None:
        return b''
    return bytes(byte_data)

def _encode_str(item:str):
    bytes_data = str.encode(item)
    return ('s', len(bytes_data)), memoryview(bytes_data)

def _decode_str(byte_data:memoryview, info:tuple):
    if byte_data is None:
        return ''
    return bytes(byte_data).decode()

def _encode_ndarray(item:np.ndarray):
    byte_data = item.tobytes()
    return ('n', len(byte_data), item.dtype, item.shape), memoryview(byte_data)

def _decode_ndarray(byte_data:memoryview, info:tuple):
    return np.frombuffer(byte_data, dtype=info[2]).copy().reshape(info[3])

def _encode_ndtype(item:np.dtype):
    byte_data = str.encode(str(item))
    return ('t', len(byte_data)), memoryview(byte_data)

def _decode_ndtype(byte_data:memoryview, info:tuple):
    return np.dtype(bytes(byte_data).decode())

iterables = {
    tuple: 'p',
    list: 'l',
    dict: 'd',
    set: 'u',
}

encoder = {
    bytes: _encode_bytes,
    int: _encode_int,
    float: _encode_float,
    bool: _encode_bool,
    str: _encode_str,
    np.ndarray: _encode_ndarray,
}
decoder = {
    'r': _decode_bytes,
    'i': _decode_int,
    'f': _decode_float,
    'b': _decode_bool,
    's': _decode_str,
    'n': _decode_ndarray,
}

def encode(item):
    if item is None:
        return (None, 0), b''
    
    datatype = type(item)
    enc = encoder.get(datatype, None)
    if enc is not None:
        return enc(item)
    
    if isinstance(item, np.dtype):
        return _encode_ndtype(item)

    raise ValueError(f"Unsupported data type: {type(item)}")

def decode(bytes_data:memoryview, info:tuple):
        if info[0] is None:
            return None
        
        dec = decoder.get(info[0], None)
        if dec is not None:
            return dec(bytes_data, info)
        
        if info[0] == 't':
            return _decode_ndtype(bytes_data, info)
        
        raise ValueError(f"Unsupported data type: {info[0]}")
