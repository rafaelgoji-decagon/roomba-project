"""Create the fixed-scale ArUco origin board used by the Roomba camera."""

from pathlib import Path

import cv2
from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "roomba-origin-aruco-board.pdf"
TEMP = ROOT / "tmp" / "pdfs" / "aruco-origin"
MARKER_MM = 70.0
GAP_MM = 12.0


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    TEMP.mkdir(parents=True, exist_ok=True)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    marker_paths = []
    for marker_id in range(4):
        image = cv2.aruco.generateImageMarker(dictionary, marker_id, 1200, borderBits=1)
        path = TEMP / f"marker-{marker_id}.png"
        if not cv2.imwrite(str(path), image):
            raise RuntimeError(f"Unable to write {path}")
        marker_paths.append(path)

    page_width, page_height = letter
    pdf = canvas.Canvas(str(OUTPUT), pagesize=letter, pageCompression=1)
    pdf.setTitle("Roomba - Origin ArUco Board")
    pdf.setAuthor("Roomba Project")
    pdf.setFillColor(HexColor("#1D1D1F"))
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(20 * mm, page_height - 23 * mm, "Roomba - marcador de origen")
    pdf.setFillColor(HexColor("#6E6E73"))
    pdf.setFont("Helvetica", 10)
    pdf.drawString(20 * mm, page_height - 31 * mm, "Tablero ArUco 4x4 | IDs 0-3 | Imprimir en tamano real (100%)")

    board_size = 2 * MARKER_MM + GAP_MM
    board_left = (page_width - board_size * mm) / 2
    board_bottom = 58 * mm
    pdf.setFillColor(HexColor("#FFFFFF"))
    pdf.roundRect(board_left - 8 * mm, board_bottom - 8 * mm, (board_size + 16) * mm, (board_size + 16) * mm, 4 * mm, fill=1, stroke=0)
    for marker_id, marker_path in enumerate(marker_paths):
        column = marker_id % 2
        row = 1 - marker_id // 2
        x = board_left + column * (MARKER_MM + GAP_MM) * mm
        y = board_bottom + row * (MARKER_MM + GAP_MM) * mm
        pdf.drawImage(str(marker_path), x, y, MARKER_MM * mm, MARKER_MM * mm, preserveAspectRatio=True, mask="auto")

    pdf.setFillColor(HexColor("#1D1D1F"))
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawCentredString(page_width / 2, 45 * mm, "No recortar los marcadores. Pegar toda la hoja completamente plana.")
    pdf.setFillColor(HexColor("#6E6E73"))
    pdf.setFont("Helvetica", 9)
    pdf.drawCentredString(page_width / 2, 39 * mm, "Cada cuadrado negro debe medir exactamente 70.0 mm por lado.")

    ruler_left = (page_width - 100 * mm) / 2
    ruler_y = 25 * mm
    pdf.setStrokeColor(black)
    pdf.setLineWidth(0.8)
    pdf.line(ruler_left, ruler_y, ruler_left + 100 * mm, ruler_y)
    for offset in (0, 50, 100):
        x = ruler_left + offset * mm
        pdf.line(x, ruler_y - 2 * mm, x, ruler_y + 2 * mm)
    pdf.setFillColor(HexColor("#6E6E73"))
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(page_width / 2, 19 * mm, "Linea de verificacion: 100.0 mm")
    pdf.save()
    print(OUTPUT)


if __name__ == "__main__":
    main()
