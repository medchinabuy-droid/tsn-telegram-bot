# Backport of imghdr for Python 3.13 compatibility

def what(file, h=None):
    if h is None:
        try:
            with open(file, 'rb') as f:
                h = f.read(32)
        except Exception:
            return None

    if h.startswith(b'\xff\xd8'):
        return 'jpeg'
    if h.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if h[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if h.startswith(b'BM'):
        return 'bmp'
    if h.startswith(b'%PDF'):
        return 'pdf'
    return None
