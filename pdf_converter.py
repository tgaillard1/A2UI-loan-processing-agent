import base64
import os
import fitz


def convert_pdf_to_base64_png(pdf_file_path: str) -> str:
  """Renders the first page of a PDF to a high-definition base64 PNG Data URI.

  Args:
      pdf_file_path: Absolute path to the local PDF file.
  """
  print(
      f"[PDF-Converter] Converting local PDF to base64 PNG: {pdf_file_path}..."
  )
  if not os.path.exists(pdf_file_path):
    raise FileNotFoundError(
        f"Local PDF file not found at path: {pdf_file_path}"
    )

  try:
    # 1. Open PDF
    doc = fitz.open(pdf_file_path)
    page = doc[0]

    # 2. Render to Pixmap at 2.0x zoom for crystal-clear small text readability
    zoom = 2.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)

    # 3. Convert to bytes and encode to base64
    png_bytes = pix.tobytes("png")
    doc.close()

    base64_encoded = base64.b64encode(png_bytes).decode("utf-8")
    data_uri = f"data:image/png;base64,{base64_encoded}"

    print(
        "[PDF-Converter] Conversion successful! Data URI generated (Length:"
        f" {len(data_uri)} chars)"
    )
    return data_uri
  except Exception as e:
    print(f"[PDF-Converter] ERROR during PDF conversion: {e}")
    raise e
