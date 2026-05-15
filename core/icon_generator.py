# core/icon_generator.py
# Gera ícones .ico com label centralizado (ex.: "M", "1", "2", ...) usando Pillow.
# Usado pra dar uma identidade visual a cada instância MT5 portable.

import logging
import os

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


# Cores do badge — master destacado em laranja, slaves em azul.
_MASTER_BG = (255, 140, 0, 255)   # laranja
_SLAVE_BG = (52, 120, 246, 255)   # azul
_TEXT_COLOR = (255, 255, 255, 255)


def _pick_font(size: int):
    """Tenta fontes do sistema na ordem; cai pra default se nada existir."""
    candidates = [
        "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf",
        "LiberationSans-Bold.ttf", "Helvetica-Bold.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def generate_label_ico(label: str, output_path: str, is_master: bool = False) -> bool:
    """Gera um .ico com o `label` centralizado e salva em `output_path`.

    Retorna True se gravou com sucesso, False caso contrário.
    Silenciosamente falha se Pillow não está instalado.
    """
    if not _PIL_AVAILABLE:
        logger.warning("Pillow não instalado — ícone não gerado.")
        return False

    sizes = [16, 32, 48, 64, 128, 256]
    images = []
    bg = _MASTER_BG if is_master else _SLAVE_BG

    for size in sizes:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Círculo de fundo
        draw.ellipse((0, 0, size - 1, size - 1), fill=bg)
        # Texto centralizado
        font_size = int(size * 0.55) if len(label) == 1 else int(size * 0.45)
        font = _pick_font(font_size)
        try:
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            tx = (size - tw) / 2 - bbox[0]
            ty = (size - th) / 2 - bbox[1]
        except AttributeError:
            tw, th = draw.textsize(label, font=font)
            tx = (size - tw) / 2
            ty = (size - th) / 2
        draw.text((tx, ty), label, fill=_TEXT_COLOR, font=font)
        images.append(img)

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        # A imagem-base do save tem que ser a MAIOR: o encoder ICO do PIL
        # descarta qualquer `size` maior que a imagem-base. Salvar com a de
        # 256x256 como base garante que todos os frames (16→256) entrem no .ico.
        images[-1].save(
            output_path,
            format="ICO",
            sizes=[(s, s) for s in sizes],
            append_images=images[:-1],
        )
        logger.debug(f"Ícone '{label}' salvo em {output_path}")
        return True
    except Exception as e:
        logger.error(f"Falha ao salvar ícone em {output_path}: {e}")
        return False
