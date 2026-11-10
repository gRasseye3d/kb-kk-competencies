import html as html_module
import re
import zlib

FONT_REG = "F1"
FONT_BOLD = "F2"
FONT_MONO = "F3"

def _escape(s):
    s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return "".join(ch if 32 <= ord(ch) < 256 else "?" for ch in s)

def _wrap(text, width_chars):
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width_chars:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return out

def _strip_html(html: str):
    html = re.sub(r"<\s*(br|/p|/div|/h\d|/li|/tr|/pre)[^>]*>", "\n",
                  html, flags=re.I)
    html = re.sub(r"<h1[^>]*>(.*?)</h1>",
                  lambda m: f"\x01H1:{m.group(1)}\n", html, flags=re.I | re.S)
    html = re.sub(r"<h2[^>]*>(.*?)</h2>",
                  lambda m: f"\x01H2:{m.group(1)}\n", html, flags=re.I | re.S)
    html = re.sub(r"<h3[^>]*>(.*?)</h3>",
                  lambda m: f"\x01H3:{m.group(1)}\n", html, flags=re.I | re.S)
    html = re.sub(r"<pre[^>]*>(.*?)</pre>",
                  lambda m: f"\x01CODE:{m.group(1)}\n", html, flags=re.I | re.S)
    html = re.sub(r"<li[^>]*>(.*?)</li>", r"• \1\n", html, flags=re.I | re.S)
    html = re.sub(r"<[^>]+>", "", html)
    text = html_module.unescape(html)
    return [ln.rstrip() for ln in text.split("\n") if ln.strip()]

class PDFBuilder:
    def __init__(self):
        self.pages = []
        self.current = []
        self.y = 800
        self.margin = 50
        self.page_h = 842
        self.page_w = 595

    def new_page(self):
        if self.current:
            self.pages.append(self.current)
        self.current = []
        self.y = self.page_h - self.margin

    def add_line(self, text, font=FONT_REG, size=11, indent=0):
        if self.y < self.margin + size:
            self.new_page()
        self.current.append((font, self.margin + indent, self.y, size, text))
        self.y -= size * 1.35

    def add_paragraph(self, text, font=FONT_REG, size=11):
        for line in _wrap(text, 95):
            self.add_line(line, font, size)

    def add_code(self, code):
        for line in code.split("\n"):
            self.add_line(line[:100], FONT_MONO, 9, indent=10)

    def build(self, title="Документ"):
        self.new_page()
        objs = []

        def add(o):
            objs.append(o)
            return len(objs)

        font_reg = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                       b"/Encoding /WinAnsiEncoding >>")
        font_bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                        b"/Encoding /WinAnsiEncoding >>")
        font_mono = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier "
                        b"/Encoding /WinAnsiEncoding >>")

        pages_id_placeholder = 0
        kids_ids = []
        for lines in self.pages:
            content = ["BT"]
            for font, x, y, size, text in lines:
                content.append(
                    f"/{font} {size} Tf 1 0 0 1 {x} {y} Tm ({_escape(text)}) Tj"
                )
            content.append("ET")
            stream = "\n".join(content).encode("latin-1", "replace")
            comp = zlib.compress(stream)
            cid = add(
                b"<< /Length " + str(len(comp)).encode() +
                b" /Filter /FlateDecode >>\nstream\n" + comp + b"\nendstream"
            )
            page_obj = (
                b"<< /Type /Page /Parent PAGES_ID 0 R /MediaBox [0 0 595 842] "
                b"/Resources << /Font << /F1 " + str(font_reg).encode() +
                b" 0 R /F2 " + str(font_bold).encode() +
                b" 0 R /F3 " + str(font_mono).encode() +
                b" 0 R >> >> /Contents " + str(cid).encode() + b" 0 R >>"
            )
            pid = add(page_obj)
            kids_ids.append(pid)

        pages_id = len(objs) + 1
        kids_ref = " ".join(f"{k} 0 R" for k in kids_ids).encode()
        pages_obj = add(b"<< /Type /Pages /Kids [" + kids_ref +
                        b"] /Count " + str(len(kids_ids)).encode() + b" >>")
        catalog_id = add(b"<< /Type /Catalog /Pages " +
                         str(pages_obj).encode() + b" 0 R >>")

        fixed_objs = []
        for o in objs:
            fixed_objs.append(o.replace(b"PAGES_ID", str(pages_id).encode()))

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for i, obj in enumerate(fixed_objs, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
        xref_pos = len(out)
        out += f"xref\n0 {len(fixed_objs)+1}\n".encode()
        out += b"0000000000 65535 f \n"
        for off in offsets[1:]:
            out += f"{off:010d} 00000 n \n".encode()
        out += (f"trailer << /Size {len(fixed_objs)+1} /Root {catalog_id} 0 R >>\n"
                f"startxref\n{xref_pos}\n%%EOF").encode()
        return bytes(out)

def render_page_to_pdf(title: str, html: str) -> bytes:
    b = PDFBuilder()
    b.add_line(title, FONT_BOLD, 20)
    b.add_line("")
    for line in _strip_html(html):
        if line.startswith("\x01H1:"):
            b.add_line(line[4:], FONT_BOLD, 16)
        elif line.startswith("\x01H2:"):
            b.add_line(line[4:], FONT_BOLD, 14)
        elif line.startswith("\x01H3:"):
            b.add_line(line[4:], FONT_BOLD, 12)
        elif line.startswith("\x01CODE:"):
            b.add_code(line[6:])
        elif line.startswith("• "):
            b.add_line("  " + line, FONT_REG, 11)
        else:
            b.add_paragraph(line)
    return b.build(title)
