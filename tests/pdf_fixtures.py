"""Generators for the PDF layouts that historically broke PDF->DOCX.

Each function builds a small synthetic PDF with PyMuPDF and returns its path.
They are the regression harness for converters/pdf_docx_fixup.py — every
layout here reproduced a real formatting bug at some point:

* ``make_grid_letterhead``   left|center|right header row on every page +
                             centered footer with a page number (used to
                             become body tables repeated per page, and each
                             page spilled onto two).
* ``make_form_letterhead``   misaligned letterhead (blocks at different
                             heights, title below), rule, label/value form
                             lines and numbered clauses (used to become
                             strangled 1x2 tables with negative usable width
                             and merged clause paragraphs).
* ``make_tab_columns``       borderless label/number columns (used to lose
                             column alignment entirely).
* ``make_lattice_table``     ruled 3x3 table with distinct column widths
                             (pdf2docx used to emit equal thirds).
"""

import fitz


def make_grid_letterhead(path: str, pages: int = 3) -> str:
    W, H = 595, 842
    doc = fitz.open()
    body = (
        "El presente documento describe los resultados del trimestre y "
        "las acciones acordadas por el comite directivo."
    )
    for pg in range(pages):
        page = doc.new_page(width=W, height=H)
        page.insert_text((40, 40), "ACME S.A. de C.V.", fontsize=11, fontname="hebo")
        page.insert_text((40, 54), "Av. Reforma 123, CDMX", fontsize=8)
        title = "Informe Trimestral 2026"
        tw = fitz.get_text_length(title, fontname="hebo", fontsize=13)
        page.insert_text(((W - tw) / 2, 46), title, fontsize=13, fontname="hebo")
        right = f"16/07/2026 - Pag. {pg + 1} de {pages}"
        rw = fitz.get_text_length(right, fontname="helv", fontsize=9)
        page.insert_text((W - 40 - rw, 46), right, fontsize=9)
        page.draw_line(fitz.Point(40, 66), fitz.Point(W - 40, 66), width=0.8)

        y = 100.0
        page.insert_text(
            (40, y), f"Seccion {pg + 1}. Resumen ejecutivo", fontsize=13, fontname="hebo"
        )
        y += 24
        for i in range(42):
            page.insert_text((40, y), f"{pg + 1}.{i:02d}  {body[:88]}", fontsize=10)
            y += 13.5

        foot = f"Documento confidencial - pagina {pg + 1}"
        fw = fitz.get_text_length(foot, fontname="helv", fontsize=8)
        page.insert_text(((W - fw) / 2, H - 30), foot, fontsize=8)
    doc.save(path)
    doc.close()
    return path


def make_form_letterhead(path: str, pages: int = 2) -> str:
    W, H = 595, 842
    doc = fitz.open()
    for pg in range(pages):
        page = doc.new_page(width=W, height=H)
        page.insert_text((40, 42), "NOTARIA 88", fontsize=12, fontname="hebo")
        page.insert_text((40, 55), "Lic. Ana Gomez Diaz", fontsize=8)
        for i, t in enumerate(["Fecha: 16/07/2026", f"Expediente: 123/2026-{pg + 1}"]):
            tw = fitz.get_text_length(t, fontname="helv", fontsize=9)
            page.insert_text((W - 40 - tw, 44 + i * 12), t, fontsize=9)
        title = "ACTA DE ENTREGA"
        tw = fitz.get_text_length(title, fontname="hebo", fontsize=14)
        page.insert_text(((W - tw) / 2, 84), title, fontsize=14, fontname="hebo")
        page.draw_line(fitz.Point(40, 98), fitz.Point(W - 40, 98), width=0.8)

        y = 130.0
        for label, value in [
            ("Nombre:", "Juan Perez Lopez"),
            ("Domicilio:", "Calle Falsa 123, Col. Centro"),
            ("Telefono:", "55-1234-5678"),
            ("Correo:", "juan.perez@example.com"),
            ("Observaciones:", "Sin observaciones adicionales."),
        ]:
            page.insert_text((40, y), label, fontsize=10, fontname="hebo")
            page.insert_text((150, y), value, fontsize=10)
            y += 22
        y += 10
        page.insert_text((40, y), "CLAUSULAS", fontsize=11, fontname="hebo")
        y += 18
        for i in range(6):
            page.insert_text(
                (40, y), f"{i + 1}. Clausula numero {i + 1} del presente documento.", fontsize=10
            )
            y += 15
    doc.save(path)
    doc.close()
    return path


def make_tab_columns(path: str) -> str:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), "Reporte de inventario", fontsize=14, fontname="hebo")
    rows = [
        ("Concepto", "Cantidad", "Precio unitario"),
        ("Tornillos hexagonales de acero", "250", "1.50"),
        ("Pintura vinilica blanca 4L", "12", "289.00"),
        ("Cable THW calibre 12 (m)", "180", "9.80"),
    ]
    y = 110
    for r in rows:
        page.insert_text((40, y), r[0], fontsize=10)
        page.insert_text((300, y), r[1], fontsize=10)
        page.insert_text((430, y), r[2], fontsize=10)
        y += 18
    page.insert_text(
        (40, y + 20), "Observaciones: inventario levantado el 14 de julio.", fontsize=10
    )
    doc.save(path)
    doc.close()
    return path


def make_official_letter(path: str) -> str:
    """Official-letter layout (Infonavit-style): centered institution header,
    red logo top-right, red banner with white text, asymmetric two-column
    zone (personal data left / place-date-number right), recipient block,
    letter-spaced PRESENTE, justified body. pdf2docx used to model the
    two-column zone as w:cols sections (scrambled + page-split render) and
    lose the banner fill (white-on-white text)."""
    import textwrap

    RED = (0.85, 0.04, 0.15)
    doc = fitz.open()
    W, H = 612, 792
    page = doc.new_page(width=W, height=H)

    t1 = "Instituto del Fondo Nacional de la Vivienda para los Trabajadores"
    w1 = fitz.get_text_length(t1, fontname="hebo", fontsize=10)
    page.insert_text(((W - w1) / 2, 58), t1, fontsize=10, fontname="hebo")
    t2 = "Gerencia de Recaudacion Fiscal y Cobranza"
    w2 = fitz.get_text_length(t2, fontname="helv", fontsize=9)
    page.insert_text(((W - w2) / 2, 72), t2, fontsize=9)
    page.draw_circle(fitz.Point(W - 85, 62), 20, color=RED, fill=RED)
    page.draw_rect(fitz.Rect(W - 112, 84, W - 58, 90), color=RED, fill=RED)

    page.draw_rect(fitz.Rect(100, 132, 268, 152), color=RED, fill=RED)
    page.insert_text((107, 146), "Datos personales", fontsize=10, fontname="hebo", color=(1, 1, 1))
    y = 168.0
    for line in [
        "GARCIA LOPEZ JUAN ALBERTO",
        "NSS: 12345678901",
        "RFC: GALJ850101AB0",
        "CURP: GALJ850101HNLRPN09",
        "Credito: 2109876543",
    ]:
        page.insert_text((100, y), line, fontsize=9)
        y += 12.5

    lbl = "Lugar y fecha de emision"
    wl = fitz.get_text_length(lbl, fontname="helv", fontsize=9)
    cx = 468
    page.insert_text((cx - wl / 2, 140), lbl, fontsize=9)
    page.draw_line(fitz.Point(368, 148), fitz.Point(568, 148), color=RED, width=1)
    for i, (txt, bold) in enumerate(
        [
            ("MONTERREY, NUEVO LEON", False),
            ("a 15 de julio de 2025", False),
            ("Numero", False),
            ("2109876543-XY-0098123456", True),
        ]
    ):
        fn = "hebo" if bold else "helv"
        tw = fitz.get_text_length(txt, fontname=fn, fontsize=9)
        page.insert_text((cx - tw / 2, 162 + i * 13), txt, fontsize=9, fontname=fn)

    y = 250.0
    for line in [
        "JUAN ALBERTO GARCIA LOPEZ",
        "CALLE FALSA 123, INT. 4",
        "COLONIA CENTRO, C.P. 64000, MONTERREY, NUEVO LEON",
    ]:
        page.insert_text((100, y), line, fontsize=9, fontname="hebo")
        y += 12.5
    page.insert_text((100, 302), "P R E S E N T E", fontsize=10, fontname="hebo")

    body = (
        "Por medio de la presente y en atencion a su solicitud, se hace "
        "constar que el credito otorgado en su favor por este Instituto, "
        "destinado a la adquisicion de la vivienda ubicada en el "
        "domicilio senalado, con fecha 30 de junio de 2025 quedo "
        "totalmente liquidado, por lo que se extiende la presente carta "
        "de instruccion para la cancelacion de la hipoteca constituida "
        "sobre el inmueble de referencia ante el Registro Publico de la "
        "Propiedad y del Comercio del Estado de Nuevo Leon."
    )
    y = 330.0
    for ln in textwrap.wrap(body, width=98):
        page.insert_text((100, y), ln, fontsize=9)
        y += 12.0
    page.insert_text((100, y + 40), "A T E N T A M E N T E", fontsize=9, fontname="hebo")
    page.insert_text((100, y + 80), "LIC. MARIA FERNANDEZ RUIZ", fontsize=9)
    page.insert_text((100, y + 92), "GERENTE DE COBRANZA", fontsize=8)
    doc.save(path)
    doc.close()
    return path


LATTICE_COLS = [40, 260, 380, 520]  # real column widths: 220 / 120 / 140 pt


def make_lattice_table(path: str) -> str:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), "Tabla con bordes", fontsize=14, fontname="hebo")
    rows_y = [100, 124, 148, 172]
    for y in rows_y:
        page.draw_line(fitz.Point(LATTICE_COLS[0], y), fitz.Point(LATTICE_COLS[-1], y), width=0.7)
    for x in LATTICE_COLS:
        page.draw_line(fitz.Point(x, rows_y[0]), fitz.Point(x, rows_y[-1]), width=0.7)
    data = [
        ("Articulo", "Unidades", "Importe"),
        ("Cemento gris 50kg", "40", "7,600.00"),
        ("Varilla 3/8 (pza)", "120", "18,240.00"),
    ]
    # rows_y and LATTICE_COLS hold the grid lines, one more than the cells
    for r, y in zip(data, rows_y, strict=False):
        for text, x in zip(r, LATTICE_COLS, strict=False):
            page.insert_text((x + 6, y + 16), text, fontsize=10)
    page.insert_text((40, 220), "Texto posterior a la tabla.", fontsize=10)
    doc.save(path)
    doc.close()
    return path
