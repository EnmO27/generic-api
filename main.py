import matplotlib
matplotlib.use('Agg')

from PIL import Image as PILImage

from fastapi import Body, FastAPI, UploadFile, File, Response, HTTPException, BackgroundTasks, Form, Request, Query
from fastapi.responses import StreamingResponse, PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from reportlab.platypus import SimpleDocTemplate, BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether, LongTable
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.colors import black, grey
from reportlab.lib import colors
from reportlab.lib.units import cm

from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics import renderPDF

import matplotlib.pyplot as plt
import numpy as np
from svglib.svglib import svg2rlg

#from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from middlewares import ForwardedProtoMiddleware  # Importa tu clase personalizada

from weasyprint import HTML
from pdf2image import convert_from_bytes
from io import BytesIO

from zipfile import ZipFile, ZIP_DEFLATED
from pydantic import BaseModel
from typing import List, Dict, Any

import base64
import httpx
import zipfile
import os
import logging
import asyncio
import tempfile
import uuid
import shutil
import cairosvg
from fastapi import status

print("PORT:", os.environ.get("PORT"))
logging.basicConfig(level=logging.INFO)
logging.info(f"PORT env: {os.environ.get('PORT')}")

app = FastAPI()
app.add_middleware(ForwardedProtoMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permitir cualquier origen
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Almacenamiento temporal de estados
job_status: Dict[str, str] = {}  # jobId -> "pendiente" | "procesando" | "completado" | "error"
job_errors: Dict[str, str] = {}  # jobId -> mensaje de error (si hay)
zip_folder = "/tmp/zip_jobs"
os.makedirs(zip_folder, exist_ok=True)

@app.on_event("startup")
async def on_startup():
    port = os.environ.get("PORT")
    print(f"🚀 Servidor iniciado. Escuchando en puerto: {port}")
    logging.basicConfig(level=logging.INFO)
    logging.info(f"PORT env: {os.environ.get('PORT')}")


@app.post("/pdf-to-images/")
async def pdf_to_images(file: UploadFile = File(...)):
    """
    Recibe un PDF y devuelve sus páginas como imágenes en base64.
    """
    try:
        contents = await file.read()
        images = convert_from_bytes(contents)
        images_base64 = []
        for img in images:
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            images_base64.append(img_str)
        return {"images": images_base64}
    except Exception as e:
        return {"error": str(e)}

@app.post("/pdf-to-images-upload/")
async def pdf_to_images_upload(
    file: UploadFile = File(...),
    upload_url: str = File(..., description="URL destino para subir el archivo"),
    file_field_name: str = File("file", description="Nombre del campo en formData")
    ):
    """
    Recibe un PDF, convierte cada página a imagen PNG y las sube como archivos binarios a un endpoint externo.
    Devuelve la respuesta del endpoint por cada página enviada.
    """
    try:
        contents = await file.read()
        images = convert_from_bytes(contents)
        if not images:
            return {"error": "No se pudo convertir el PDF a imagen."}

        responses = []
        async with httpx.AsyncClient() as client:
            for idx, img in enumerate(images):
                buffered = BytesIO()
                img.save(buffered, format="PNG")
                buffered.seek(0)
                files = {
                    file_field_name: (f"{file.filename}_page{idx+1}.png", buffered, "image/png")
                }
                response = await client.post(upload_url, files=files)
                responses.append({
                    "page": idx + 1,
                    "status_code": response.status_code,
                    "response": response.text
                })

        return {"results": responses}
    except Exception as e:
        return {"error": str(e)}
    
class HtmlToPdfUploadRequest(BaseModel):
    html: str
    upload_url: str
    file_field_name: str = "file"
    filename: str = "document.pdf"
    language: str = "es"  # "es" para español, "en" para inglés

@app.post("/html-to-pdf-upload/")
async def html_to_pdf_upload(request: HtmlToPdfUploadRequest):
    """
    Recibe HTML, lo convierte a PDF con header/footer en español o inglés y sube el PDF a un endpoint externo.
    El footer muestra correctamente el número de página y el total de páginas.
    Si ocurre un error al subir el PDF, devuelve el error detallado.
    """
    try:
        # Definir textos según idioma
        if request.language.lower() == "en":
            header_title = "CURRENT TRANSFORMER TEST REPORT"
            company_name = "EQUIPOS ELÉCTRICOS CORE S.A de C.V"
            address1 = "Mirto 36, Lomas de San Miguel, 52928,"
            address2 = "Cd López Mateos, Méx"
            phone = "Tel, +(52) 55 58 87 08 71"
            footer_left = "January 2025 REV-A"
            footer_center = "Page"
            footer_right = "FIT: 132"
        else:
            header_title = "REPORTE DE PRUEBAS A TRANSFORMADOR DE CORRIENTE"
            company_name = "EQUIPOS ELÉCTRICOS CORE S.A de C.V"
            address1 = "Mirto 36, Lomas de San Miguel, 52928,"
            address2 = "Cd López Mateos, Méx"
            phone = "Tel, +(52) 55 58 87 08 71"
            footer_left = "Enero 2025 REV-A"
            footer_center = "Página"
            footer_right = "FIT: 132"

        margin_css = f"""
        <style>
        @page {{
            margin: 0cm;
            margin-top: 4.5cm;
            margin-bottom: 2.5cm;
            @top-center {{
                content: element(header);
            }}
            @bottom-left {{
                content: "{footer_left}";
                color: #888;
                font-size: 10px;
                font-family: Arial, sans-serif;
                margin-left: 2cm;
            }}
            @bottom-center {{
                content: "{footer_center} " counter(page) " / " counter(pages);
                color: #888;
                font-size: 10px;
                font-family: Arial, sans-serif;
            }}
            @bottom-right {{
                content: "{footer_right}";
                color: #888;
                font-size: 10px;
                font-family: Arial, sans-serif;
                margin-right: 2cm;

            }}
        }}
        #header {{
            display: grid;
            grid-template-columns: 25% 50% 25%;
            position: running(header);
            width: 18cm;
            height: 3cm;
            background: white;
            font-family: Arial, sans-serif;
        }}
        #footer {{
            display: grid;
            grid-template-columns: 25% 50% 25%;
            width: 20cm;
            text-align: center;
            font-size: 10px;
            color: #888;
            position: running(footer);
            height: 1.5cm;
            background: white;
            font-family: Arial, sans-serif;
        }}
        #footer-center {{
            width: 100%;
            background: white;
        }}
        </style>
        <div id="header">
            <div>
                <img
                    src="https://tendero.blob.core.windows.net/fileseecore/c124a0bb-c546-48f9-a9c9-56443f1554b4.png?sv=2024-08-04&st=2025-06-21T17%3A48%3A11Z&se=2125-06-21T17%3A48%3A11Z&sr=b&sp=r&sig=sQisxXSu5LkJG7RDfSD%2BcV9anlamuqW9X4W4dp8J%2BZ4%3D"
                    style="width: 100px" />
            </div>
            <div style="display: flex; justify-content: center; text-align: center;">
                <h3 style="font-weight: bold; color: black; font-size: 20px; margin: 0;">
                    {header_title}
                </h3>
            </div>
            <div>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {company_name}
                </p>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {address1}
                </p>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {address2}
                </p>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {phone}
                </p>
            </div>
        </div>
        <div id="footer">
            <div style="background: white; width: 100%">
                <p>{footer_left}</p>
            </div>
            <div id="footer-center">
                <p style="margin:0;">
                    <span style="font-variant-numeric: tabular-nums;">{footer_center}</span>
                </p>
            </div>
            <div style="background: white; width: 100%">
                <p>{footer_right}</p>
            </div>
        </div>
        """
        html_with_margin = margin_css + request.html

        pdf_buffer = BytesIO()
        HTML(string=html_with_margin).write_pdf(pdf_buffer)
        pdf_buffer.seek(0)

        async with httpx.AsyncClient(follow_redirects=True) as client:
            files = {
                request.file_field_name: (request.filename, pdf_buffer, "application/pdf")
            }
            try:
                response = await client.post(request.upload_url, files=files)
            except httpx.HTTPError as exc:
                return {
                    "error": "Error al subir el PDF",
                    "detail": str(exc)
                }

            if response.status_code in [301, 302, 307, 308]:
                return {
                    "error": "Redirección detectada",
                    "status_code": response.status_code,
                    "location": response.headers.get("location", "no header")
                }

            if response.status_code >= 400:
                return {
                    "error": "Error al subir el PDF",
                    "status_code": response.status_code,
                    "response": response.text
                }

            result = {
                "status_code": response.status_code,
                "response": response.text
            }
        return result
    except Exception as e:
        return {"error": str(e)}
    
class HtmlsToPdfsZipUploadRequest(BaseModel):
    htmls: List[str]
    upload_url: str
    file_field_name: str = "file"
    filename: str = "documents.zip"
    language: str = "es"
    offf: str
    
@app.post("/htmls-to-pdfs-zip-upload-stream/")
async def htmls_to_pdfs_zip_upload_stream(request: HtmlsToPdfsZipUploadRequest):
    """
    Recibe un array de HTMLs, los convierte a PDFs y devuelve un ZIP con todos los PDFs como respuesta.
    """
    try:
        # Definir textos según idioma
        if request.language.lower() == "en":
            header_title = "CURRENT TRANSFORMER TEST REPORT"
            company_name = "EQUIPOS ELÉCTRICOS CORE S.A de C.V"
            address1 = "Mirto 36, Lomas de San Miguel, 52928,"
            address2 = "Cd López Mateos, Méx"
            phone = "Tel, +(52) 55 58 87 08 71"
            footer_left = "January 2025 REV-A"
            footer_center = "Page"
            footer_right = "FIT: 132"
        else:
            header_title = "REPORTE DE PRUEBAS A TRANSFORMADOR DE CORRIENTE"
            company_name = "EQUIPOS ELÉCTRICOS CORE S.A de C.V"
            address1 = "Mirto 36, Lomas de San Miguel, 52928,"
            address2 = "Cd López Mateos, Méx"
            phone = "Tel, +(52) 55 58 87 08 71"
            footer_left = "Enero 2025 REV-A"
            footer_center = "Página"
            footer_right = "FIT: 132"
            
        margin_css = f"""
        <style>
        @page {{
            margin: 0cm;
            margin-top: 4.5cm;
            margin-bottom: 2.5cm;
            @top-center {{
                content: element(header);
            }}
            @bottom-left {{
                content: "{footer_left}";
                color: #888;
                font-size: 10px;
                font-family: Arial, sans-serif;
                margin-left: 2cm;
            }}
            @bottom-center {{
                content: "{footer_center} " counter(page) " / " counter(pages);
                color: #888;
                font-size: 10px;
                font-family: Arial, sans-serif;
            }}
            @bottom-right {{
                content: "{footer_right}";
                color: #888;
                font-size: 10px;
                font-family: Arial, sans-serif;
                margin-right: 2cm;
            }}
        }}
        #header {{
            display: grid;
            grid-template-columns: 25% 50% 25%;
            position: running(header);
            width: 18cm;
            height: 3cm;
            background: white;
            font-family: Arial, sans-serif;
        }}
        #footer {{
            display: grid;
            grid-template-columns: 25% 50% 25%;
            width: 20cm;
            text-align: center;
            font-size: 10px;
            color: #888;
            position: running(footer);
            height: 1.5cm;
            background: white;
            font-family: Arial, sans-serif;
        }}
        </style>
        <div id="header">
            <div>
                <img
                    src="https://tendero.blob.core.windows.net/fileseecore/c124a0bb-c546-48f9-a9c9-56443f1554b4.png?sv=2024-08-04&st=2025-06-21T17%3A48%3A11Z&se=2125-06-21T17%3A48%3A11Z&sr=b&sp=r&sig=sQisxXSu5LkJG7RDfSD%2BcV9anlamuqW9X4W4dp8J%2BZ4%3D"
                    style="width: 100px" />
            </div>
            <div style="display: flex; justify-content: center; text-align: center;">
                <h3 style="font-weight: bold; color: black; font-size: 20px; margin: 0;">
                    {header_title}
                </h3>
            </div>
            <div>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {company_name}
                </p>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {address1}
                </p>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {address2}
                </p>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {phone}
                </p>
            </div>
        </div>
        <div id="footer">
            <div style="background: white; width: 100%">
                <p>{footer_left}</p>
            </div>
            <div style="background: white; width: 100%">
                <p>{footer_center}</p>
            </div>
            <div style="background: white; width: 100%">
                <p>{footer_right}</p>
            </div>
        </div>
        """
        pdf_buffers = []
        # Generar PDFs en memoria
        for idx, html in enumerate(request.htmls):
            html_with_margin = margin_css + html
            pdf_buffer = BytesIO()
            HTML(string=html_with_margin).write_pdf(pdf_buffer)
            pdf_buffer.seek(0)
            # Guardar el buffer terminado para el ZIP
            pdf_buffers.append((f"{request.offf}-{idx+1}.pdf", pdf_buffer.getvalue()))

        # Crear ZIP en memoria
        zip_buffer = BytesIO()
        with ZipFile(zip_buffer, "w") as zip_file:
            for pdf_name, pdf_bytes in pdf_buffers:
                zip_file.writestr(pdf_name, pdf_bytes)
        # Importante: resetear el buffer al inicio después de cerrar el ZIP
        zip_buffer.seek(0)

        # Enviar el ZIP como respuesta al navegador
        return StreamingResponse(zip_buffer, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename={request.offf}.zip"
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/htmls-to-pdfs-zip-upload-stream-token/")
async def htmls_to_pdfs_zip_upload_stream_token(request: HtmlsToPdfsZipUploadRequest):
    """
    Recibe un array de HTMLs, los convierte a PDFs y devuelve un ZIP con todos los PDFs como respuesta.
    Soporta cancelación si request.cancel == True en cualquier punto del proceso.
    """
    try:
        # Cancelar antes de empezar
        if getattr(request, "cancel", False):
            raise HTTPException(status_code=499, detail="Solicitud cancelada por el cliente.")

        # Definir textos según idioma
        if request.language.lower() == "en":
            header_title = "CURRENT TRANSFORMER TEST REPORT"
            company_name = "EQUIPOS ELÉCTRICOS CORE S.A de C.V"
            address1 = "Mirto 36, Lomas de San Miguel, 52928,"
            address2 = "Cd López Mateos, Méx"
            phone = "Tel, +(52) 55 58 87 08 71"
            footer_left = "January 2025 REV-A"
            footer_center = "Page"
            footer_right = "FIT: 132"
        else:
            header_title = "REPORTE DE PRUEBAS A TRANSFORMADOR DE CORRIENTE"
            company_name = "EQUIPOS ELÉCTRICOS CORE S.A de C.V"
            address1 = "Mirto 36, Lomas de San Miguel, 52928,"
            address2 = "Cd López Mateos, Méx"
            phone = "Tel, +(52) 55 58 87 08 71"
            footer_left = "Enero 2025 REV-A"
            footer_center = "Página"
            footer_right = "FIT: 132"

        margin_css = f"""
        <style>
        @page {{
            margin: 0cm;
            margin-top: 4.5cm;
            margin-bottom: 2.5cm;
            @top-center {{
                content: element(header);
            }}
            @bottom-left {{
                content: "{footer_left}";
                color: #888;
                font-size: 10px;
                font-family: Arial, sans-serif;
                margin-left: 2cm;
            }}
            @bottom-center {{
                content: "{footer_center} " counter(page) " / " counter(pages);
                color: #888;
                font-size: 10px;
                font-family: Arial, sans-serif;
            }}
            @bottom-right {{
                content: "{footer_right}";
                color: #888;
                font-size: 10px;
                font-family: Arial, sans-serif;
                margin-right: 2cm;
            }}
        }}
        #header {{
            display: grid;
            grid-template-columns: 25% 50% 25%;
            position: running(header);
            width: 18cm;
            height: 3cm;
            background: white;
            font-family: Arial, sans-serif;
        }}
        #footer {{
            display: grid;
            grid-template-columns: 25% 50% 25%;
            width: 20cm;
            text-align: center;
            font-size: 10px;
            color: #888;
            position: running(footer);
            height: 1.5cm;
            background: white;
            font-family: Arial, sans-serif;
        }}
        </style>
        <div id="header">
            <div>
                <img
                    src="https://tendero.blob.core.windows.net/fileseecore/c124a0bb-c546-48f9-a9c9-56443f1554b4.png?sv=2024-08-04&st=2025-06-21T17%3A48%3A11Z&se=2125-06-21T17%3A48%3A11Z&sr=b&sp=r&sig=sQisxXSu5LkJG7RDfSD%2BcV9anlamuqW9X4W4dp8J%2BZ4%3D"
                    style="width: 100px" />
            </div>
            <div style="display: flex; justify-content: center; text-align: center;">
                <h3 style="font-weight: bold; color: black; font-size: 20px; margin: 0;">
                    {header_title}
                </h3>
            </div>
            <div>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {company_name}
                </p>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {address1}
                </p>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {address2}
                </p>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {phone}
                </p>
            </div>
        </div>
        <div id="footer">
            <div style="background: white; width: 100%">
                <p>{footer_left}</p>
            </div>
            <div style="background: white; width: 100%">
                <p>{footer_center}</p>
            </div>
            <div style="background: white; width: 100%">
                <p>{footer_right}</p>
            </div>
        </div>
        """

        pdf_buffers = []

        # Generar PDFs en memoria
        for idx, html in enumerate(request.htmls):
            # Verificar cancelación durante el proceso
            if getattr(request, "cancel", False):
                raise HTTPException(status_code=499, detail="Solicitud cancelada por el cliente durante el procesamiento.")

            html_with_margin = margin_css + html
            pdf_buffer = BytesIO()
            HTML(string=html_with_margin).write_pdf(pdf_buffer)
            pdf_buffer.seek(0)
            pdf_buffers.append((f"{request.offf}-{idx+1}.pdf", pdf_buffer.getvalue()))

        # Crear ZIP en memoria
        zip_buffer = BytesIO()
        with ZipFile(zip_buffer, "w") as zip_file:
            for pdf_name, pdf_bytes in pdf_buffers:
                zip_file.writestr(pdf_name, pdf_bytes)
        zip_buffer.seek(0)

        # Enviar el ZIP como respuesta
        return StreamingResponse(zip_buffer, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename={request.filename}"
        })

    except HTTPException as http_err:
        raise http_err
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class HtmlsToPdfsZipUploadRequest(BaseModel):
    htmls: List[str]
    file_field_name: str = "file"
    filename: str = "documents.zip"
    language: str = "es"
    offf: str

def stream_zip_file(htmls: List[str], offf: str, language: str):
    try:
        # Definir textos según idioma
        if language.lower() == "en":
            footer_left = "January 2025 REV-A"
            footer_center = "Page"
            footer_right = "FIT: 132"
            header_title = "CURRENT TRANSFORMER TEST REPORT"
            company_name = "EQUIPOS ELÉCTRICOS CORE S.A de C.V"
            address1 = "Mirto 36, Lomas de San Miguel, 52928,"
            address2 = "Cd López Mateos, Méx"
            phone = "Tel, +(52) 55 58 87 08 71"
        else:
            footer_left = "Enero 2025 REV-A"
            footer_center = "Página"
            footer_right = "FIT: 132"
            header_title = "REPORTE DE PRUEBAS A TRANSFORMADOR DE CORRIENTE"
            company_name = "EQUIPOS ELÉCTRICOS CORE S.A de C.V"
            address1 = "Mirto 36, Lomas de San Miguel, 52928,"
            address2 = "Cd López Mateos, Méx"
            phone = "Tel, +(52) 55 58 87 08 71"

        margin_css = f"""<style>
        @page {{
            margin: 0cm;
            margin-top: 4.5cm;
            margin-bottom: 2.5cm;
            @top-center {{ content: element(header); }}
            @bottom-left {{ content: "{footer_left}"; font-size: 10px; color: #888; margin-left: 2cm; }}
            @bottom-center {{ content: "{footer_center} " counter(page) " / " counter(pages); font-size: 10px; color: #888; }}
            @bottom-right {{ content: "{footer_right}"; font-size: 10px; color: #888; margin-right: 2cm; }}
        }}
        #header {{
            display: grid;
            grid-template-columns: 25% 50% 25%;
            position: running(header);
        }}
        </style>
        <div id="header">
            <div><img src="https://tendero.blob.core.windows.net/fileseecore/c124a0bb-c546-48f9-a9c9-56443f1554b4.png" style="width: 100px" /></div>
            <div style="text-align:center;"><h3>{header_title}</h3></div>
            <div style="text-align:right;">
                <p>{company_name}</p>
                <p>{address1}</p>
                <p>{address2}</p>
                <p>{phone}</p>
            </div>
        </div>
        """

        # Crear archivo ZIP en disco temporal
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        with ZipFile(temp_file.name, mode="w", compression=ZIP_DEFLATED) as zip_file:
            for idx, html in enumerate(htmls):
                full_html = margin_css + html
                pdf_io = BytesIO()
                HTML(string=full_html).write_pdf(pdf_io)
                pdf_io.seek(0)
                zip_file.writestr(f"{offf}-{idx+1}.pdf", pdf_io.read())

        # Generador de chunks de archivo
        def file_iterator(file_path, chunk_size=8192):
            with open(file_path, "rb") as f:
                while chunk := f.read(chunk_size):
                    yield chunk

        return file_iterator(temp_file.name)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando ZIP: {str(e)}")


@app.post("/htmls-to-pdfs-zip-upload-stream-yield")
async def htmls_to_pdfs_zip_upload_stream_yield(request: HtmlsToPdfsZipUploadRequest):
    zip_stream = stream_zip_file(request.htmls, request.offf, request.language)
    return StreamingResponse(
        zip_stream,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={request.filename}"}
    )

################################### GENERATE ZIP ####################
class ProtocolDataListRequest(BaseModel):
    protocolDataList: List[Dict[str, Any]]
    language: str = "es"
    filename: str = "protocols.zip"
    
@app.post("/start-pdfs-generation")
def iniciar_proceso(request: ProtocolDataListRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    job_status[job_id] = "pendiente"

    background_tasks.add_task(generate_pdfs_zip_endpoint, job_id, request)

    return {"jobId": job_id}

@app.get("/get-pdfs-status")
async def estado_proceso(jobId: str):
    estado = job_status.get(jobId)
    if not estado:
        raise HTTPException(status_code=404, detail="Proceso no encontrado")

    return {
        "jobId": jobId,
        "estado": estado,
        "error": job_errors.get(jobId) if estado == "error" else None
    }
    
@app.get("/get-pdfs-zip")
async def descargar_zip(jobId: str):
    estado = job_status.get(jobId)
    if estado != "completado":
        raise HTTPException(status_code=400, detail=f"ZIP aún no está listo. Estado actual: {estado}")

    zip_path = f"{zip_folder}/{jobId}.zip"
    if not os.path.exists(zip_path):
        raise HTTPException(status_code=404, detail="ZIP no encontrado")

    def file_iterator():
        with open(zip_path, "rb") as f:
            while chunk := f.read(8192):
                yield chunk

    return StreamingResponse(
        file_iterator(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={jobId}.zip"}
    )
    
@app.delete("/cancel-pdfs-generation")
async def cancelar_proceso(jobId: str = Query(..., description="JobId del proceso que se desea cancelar")):
    """
    Cancela y limpia un proceso de generación de PDFs en segundo plano.
    """
    if jobId not in job_status:
        raise HTTPException(status_code=404, detail="Proceso no encontrado")

    # Marcar como cancelado (opcional, para registro)
    job_status[jobId] = "cancelado"

    # Eliminar el archivo ZIP si ya se había generado
    zip_path = f"{zip_folder}/{jobId}.zip"
    if os.path.exists(zip_path):
        os.remove(zip_path)

    # Limpiar registros
    job_status.pop(jobId, None)
    job_errors.pop(jobId, None)

    return {"jobId": jobId, "detail": "Proceso cancelado y recursos limpiados correctamente"}


##################################### META MESSAGES ENDPOINT ##################

VERIFY_TOKEN = "Mktwsp231201"  # Cámbialo por uno único y seguro

@app.get("/meta-marketing-webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verificado correctamente")
        return PlainTextResponse(content=challenge, status_code=200)

    print("❌ Verificación fallida")
    return PlainTextResponse(content="Error: Verificación fallida", status_code=403)


@app.post("/meta-marketing-webhook")
async def receive_webhook(request: Request):
    body = await request.json()
    print("📩 Webhook recibido:")
    print(body)

    if body.get("object") == "whatsapp_business_account":
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                statuses = value.get("statuses", [])
                for status in statuses:
                    print("✅ Estado del mensaje:")
                    print(f"- ID: {status.get('id')}")
                    print(f"- Estado: {status.get('status')}")
                    print(f"- Teléfono: {status.get('recipient_id')}")
                    print(f"- Timestamp: {status.get('timestamp')}")

                    if "errors" in status:
                        print(f"⚠️ Errores: {status['errors']}")

    return PlainTextResponse(content="EVENT_RECEIVED", status_code=200)


WHATSAPP_TOKEN = "EAARjqUfF6oQBO7UeEh8AUQjA5YuSS5ZC8FsdNeB4LWogUZApmlWQtxtgR0axZBy3KmmHz4GMcxaNfyJxNZCRVYRb3erTARAzEdbN9SxltnX7j6ZC3SuR0RV5ZCG2tp47GHZBjIY7ZAeRqXLF2XKHpaLMzENQQpYlZBqcM93Owmka6uUFGbyUMYZC1Oiz3G7x7oRyLZCuwZDZD"
WHATSAPP_URL = "https://graph.facebook.com/v22.0/654914111039113/messages"

class WhatsappTemplateRequest(BaseModel):
    to: str
    param1: str = "Reporte Mensual de Junio"
    param2: str = "Rechazado"
    param3: str = "Kia JuanManuel"

@app.post("/send-whatsapp-template")
async def send_whatsapp_template(req: WhatsappTemplateRequest):
        payload = {
            "messaging_product": "whatsapp",
            "to": req.to,
            "type": "template",
            "template": {
                "name": "cambio_status",
                "language": { "code": "es_MX" },
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            { "type": "text", "text": req.param1 },
                            { "type": "text", "text": req.param2 },
                            { "type": "text", "text": req.param3 }
                        ]
                    }
                ]
            }
        }
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(WHATSAPP_URL, json=payload, headers=headers)
            return {
                "status_code": resp.status_code,
                "response": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            }


###############################################################################

##########################REPORT LAB #####################################

@app.post("/compress-image")
async def compress_image(file: UploadFile = File(...)):
    """
    Recibe una imagen como archivo, la comprime y redimensiona a 120x120 JPEG, y la devuelve como archivo descargable.
    """
    contents = await file.read()
    original = PILImage.open(BytesIO(contents))

    # Convertir a RGBA si tiene modo P (paleta indexada)
    if original.mode == "P":
        original = original.convert("RGBA")

    # Si tiene transparencia, agregar fondo blanco
    if original.mode in ("RGBA", "LA"):
        fondo_blanco = PILImage.new("RGB", original.size, (255, 255, 255))
        fondo_blanco.paste(original, mask=original.split()[-1])
        original = fondo_blanco
    else:
        original = original.convert("RGB")

    # Redimensionar
    resized = original.resize((120, 120))

    buffer = BytesIO()
    resized.save(buffer, format="JPEG", quality=75, optimize=True)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="image/jpeg",
        headers={"Content-Disposition": "attachment; filename=compressed.jpg"}
    )
    
    
@app.post("/compress-image-url")
async def compress_image_url(
    file: UploadFile = File(...),
    upload_url: str = Form(..., description="URL destino para subir el archivo"),
    file_field_name: str = Form("file", description="Nombre del campo en formData")
):
    """
    Recibe una imagen, la comprime y redimensiona a 120x120 JPEG, la sube a un storage externo y devuelve la URL del blob.
    """
    contents = await file.read()
    original = PILImage.open(BytesIO(contents))

    # Convertir a RGBA si tiene modo P (paleta indexada)
    if original.mode == "P":
        original = original.convert("RGBA")

    # Si tiene transparencia, agregar fondo blanco
    if original.mode in ("RGBA", "LA"):
        fondo_blanco = PILImage.new("RGB", original.size, (255, 255, 255))
        fondo_blanco.paste(original, mask=original.split()[-1])
        original = fondo_blanco
    else:
        original = original.convert("RGB")

    # Redimensionar
    resized = original.resize((120, 120))

    buffer = BytesIO()
    resized.save(buffer, format="JPEG", quality=75, optimize=True)
    buffer.seek(0)

    # Subir a storage externo
    async with httpx.AsyncClient() as client:
        files = {
            file_field_name: ("compressed.jpg", buffer, "image/jpeg")
        }
        resp = await client.post(upload_url, files=files)
        if resp.status_code >= 400:
            return {"error": f"Error al subir la imagen: {resp.status_code}", "response": resp.text}
        # Se asume que la respuesta contiene la URL del blob en JSON o texto plano
        try:
            data = resp.json()
            # Buscar la url en la respuesta (ajustar según API de storage)
            url = data.get("url") or data.get("blobUrl") or data.get("location") or next((v for v in data.values() if isinstance(v, str) and v.startswith("http")), None)
            if not url:
                url = resp.text if resp.text.startswith("http") else None
        except Exception:
            url = resp.text if resp.text.startswith("http") else None

    if not url:
        return {"error": "No se pudo obtener la URL del blob del storage", "response": resp.text}

    return {"url": url}


def generar_pdf(protocolData: Dict[str, Any], language: str = "es", output_path: str = None):
    # Crear archivo PDF temporal
    temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    temp_pdf.close()  # importante: cerrar inmediatamente

    try:
        
        ################################Convertir logo
        
        url_imagen = "https://tendero.blob.core.windows.net/fileseecore/da268367-2349-4bab-bb71-bf6e4d6e0991.jpg?sv=2024-08-04&st=2025-07-03T20%3A26%3A30Z&se=2125-07-03T20%3A26%3A30Z&sr=b&sp=r&sig=vEWx1o%2FYl%2FYiSexoIgEJ49cD63Nfs9wMtwK1KEHFFdM%3D"
        
        # Convertir el buffer a un blob y crear una URL temporal (solo posible en frontend/browser, no en backend Python puro)
        # En backend, puedes guardar el archivo y construir una URL si tienes un servidor de archivos estáticos.
        # Aquí, como ejemplo, guardamos el logo en un archivo temporal y mostramos la ruta.
        
        logo_img = Image(url_imagen, width=3*cm, height=3*cm)
        styles = getSampleStyleSheet()
        
        ##################################################

        estilo_bold = ParagraphStyle(
            name="CeldaBold",
            fontName="Helvetica-Bold",   # o "Times-Bold", "Courier-Bold"
            fontSize=8,
            alignment=TA_LEFT,
            textColor=black
        )
        
        estilo_bold_centered = ParagraphStyle(
            name="CeldaBold",
            fontName="Helvetica-Bold",   # o "Times-Bold", "Courier-Bold"
            fontSize=8,
            alignment=TA_CENTER,
            textColor=black
        )
        
        estilo_normal = ParagraphStyle(
            name="CeldaNormal",
            fontName="Helvetica",
            fontSize=8,
            alignment=TA_LEFT,
            textColor=black
        )
        
        estilo_normal_centered = ParagraphStyle(
            name="CeldaNormal",
            fontName="Helvetica",
            fontSize=8,
            alignment=TA_CENTER,
            textColor=black
        )
        
        estilo_normal_right = ParagraphStyle(
            name="CeldaNormal",
            fontName="Helvetica",
            fontSize=8,
            alignment=TA_RIGHT,
            textColor=black
        )
        
        estilo_normal_first_table = ParagraphStyle(
            name="CeldaNormal",
            fontName="Helvetica",
            fontSize=8,
            alignment=TA_CENTER,
            textColor=black
        )
        
        estilo_normal_header_center = ParagraphStyle(
            name="CeldaNormal",
            fontName="Helvetica-Bold",
            fontSize=12,
            alignment=TA_CENTER,
            textColor=black
        )
        
        estilo_normal_header_right = ParagraphStyle(
            name="CeldaNormal",
            fontName="Helvetica",
            fontSize=7,
            alignment=TA_RIGHT,
            textColor=black
        )
        
        estilo_normal_footer_left = ParagraphStyle(
            name="CeldaNormal",
            fontName="Helvetica",
            fontSize=8,
            alignment=TA_LEFT,
            textColor=grey
        )
        
        estilo_normal_footer_center = ParagraphStyle(
            name="CeldaNormal",
            fontName="Helvetica",
            fontSize=8,
            alignment=TA_CENTER,
            textColor=grey
        )
        
        estilo_normal_footer_right = ParagraphStyle(
            name="CeldaNormal",
            fontName="Helvetica",
            fontSize=8,
            alignment=TA_RIGHT,
            textColor=grey
        )
        
        fila_header = [
            logo_img,
            Paragraph("REPORTE DE PRUEBAS A TRANSFORMADOR DE CORRIENTE" if language == "es" else "CERTIFICATE TEST REPORT FOR CURRENT TRANSFORMER", estilo_normal_header_center),
            Paragraph("EQUIPOS ELÉCTRICOS CORE S.A de C.V<br/>Mirto 36, Lomas de San Miguel, 52928<br/>Cd López Mateos, Mé<br/>Tel, +(52) 55 58 87 08 71", estilo_normal_header_right),
        ]
    

        #Ancho total de A4 es 595 puntos. Quitando márgenes, deja ~540pt útiles
        tabla_header = Table([fila_header], colWidths=[160, 220, 160])  # 180 x 3 = 540 pt aprox

        tabla_header.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        
        def draw_header_footer(canvas, doc):
            canvas.saveState()

            width, height = A4
            x = (width - 540) / 2  # Centrar horizontal

            # ---------------- Encabezado ----------------
            y_header = height - 90  # desde arriba
            tabla_header.wrapOn(canvas, 540, 60)
            tabla_header.drawOn(canvas, x, y_header)

            # ---------------- Footer (como tabla) ----------------
            fila_footer = [
                Paragraph("Enero 2025 REV-A" if language == "es" else "January 2025 REV-A", estilo_normal_footer_left),
                Paragraph(f"Página {doc.page}" if language == "es" else f"Page {doc.page}", estilo_normal_footer_center),
                Paragraph("FIT: 132", estilo_normal_footer_right),
            ]

            tabla_footer = Table([fila_footer], colWidths=[180, 180, 180])
            tabla_footer.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))

            y_footer = 30  # posición desde abajo de la página
            tabla_footer.wrapOn(canvas, 540, 40)
            tabla_footer.drawOn(canvas, x, y_footer)

            canvas.restoreState()

        # elementos.append(tabla)
        
        # Estilo y contenido del PDF
        # doc = SimpleDocTemplate(temp_pdf.name, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm)
        
        doc = BaseDocTemplate(
            temp_pdf.name,
            pagesize=A4,
            rightMargin=1.5*cm,
            leftMargin=1.5*cm,
            topMargin=4*cm,   # espacio suficiente para el encabezado
            bottomMargin=1.5*cm
        )

        frame = Frame(
            doc.leftMargin,
            doc.bottomMargin,
            doc.width,
            doc.height,
            id='normal'
        )

        template = PageTemplate(id='test', frames=frame, onPage=draw_header_footer)
        doc.addPageTemplates([template])

        ancho_util = A4[0] - doc.leftMargin - doc.rightMargin
        elementos = []
        
        # Tabla de cabecera
        
        applications = [
            {
                "id": 58,
                "nameSpanish": "Protección",
                "nameEnglish": "Relay class"
            },
            {
                "id": 59,
                "nameSpanish": "Medición",
                "nameEnglish": "Metering class"
            },
            {
                "id": 95,
                "nameSpanish": "Protección / Medición",
                "nameEnglish": "Relay / Metering class"
            },
        ]
        
        transformers = [
          {
            "id": 56,
            "nameSpanish": "Bushing",
            "nameEnglish": "Bushing"
          },
          {
            "id": 57,
            "nameSpanish": "Ventana",
            "nameEnglish": "Window"
          },
          {
            "id": 88,
            "nameSpanish": "Dona",
            "nameEnglish": "Donna"
          },
          {
            "id": 89,
            "nameSpanish": "Trifasico",
            "nameEnglish": "Three-phase"
          }
        ]
        
        application_value = int(protocolData["application"])
        application_text = next((app["nameSpanish"] if language == "es" else app["nameEnglish"] for app in applications if app["id"] == application_value), "Desconocido")            
        
        first_transformer_value = int(protocolData["transformerType"])
        first_transformer_text = next((transformer["nameSpanish"] if language == "es" else transformer["nameEnglish"] for transformer in transformers if transformer["id"] == first_transformer_value), "Desconocido")            
                
        evaluation_value = protocolData["generalEvaluation"]
        if language == "es":
            evaluation_text = "CORRECTO" if evaluation_value == 1 else "INCORRECTO"
        else:
            evaluation_text = "CORRECT" if evaluation_value == 1 else "INCORRECT"
            
        datos_tabla = [
            [
            Paragraph("Dispositivo de prueba" if language == "es" else "Test device", estilo_bold), "",
            Paragraph("Fecha/hora:" if language == "es" else "Date/time:", estilo_bold),
            Paragraph(protocolData["testDevice"]["dateTime"], estilo_bold)
            ],
            [
            Paragraph("Dispositivo de prueba:" if language == "es" else "Test device:", estilo_normal),
            Paragraph(protocolData["testDevice"]["type"], estilo_normal),
            Paragraph("No serie del dispositivo:" if language == "es" else "Device serial number:", estilo_normal),
            Paragraph(protocolData["testDevice"]["serialNumber"], estilo_normal)
            ],
            [
            Paragraph("Modelo de TC:" if language == "es" else "CT model:", estilo_normal),
            Paragraph(protocolData["tcModel"], estilo_normal),
            Paragraph("No. de Serie:" if language == "es" else "Serial number:", estilo_normal),
            Paragraph(protocolData["deviceSerial"], estilo_normal)
            ],
            [
            Paragraph("Orden de Compra:" if language == "es" else "Purchase order:", estilo_normal),
            Paragraph(protocolData["purchaseOrder"], estilo_normal),
            Paragraph("Factor térmico continuo 30°C AMB:" if language == "es" else "Continuous thermal factor 30°C AMB:", estilo_normal),
            Paragraph(protocolData["thermalFactor"], estilo_normal)
            ],
            [
            Paragraph("Evaluación general:" if language == "es" else "General evaluation:", estilo_normal),
            Paragraph(evaluation_text, estilo_normal),
            Paragraph("Cliente:" if language == "es" else "Customer:", estilo_normal),
            Paragraph(protocolData["customer"], estilo_normal)
            ],
            [
            Paragraph("Cantidad:" if language == "es" else "Quantity:", estilo_normal),
            Paragraph(f"{protocolData["quantity"]} {"Pieza" if language == "es" else "Piece"}{"s" if int(protocolData["quantity"]) > 1 else ""}", estilo_normal),
            Paragraph("Clase de aislamiento:" if language == "es" else "Insulation class:", estilo_normal),
            Paragraph(str(protocolData["insulationClass"]), estilo_normal)
            ],
            [
            Paragraph("Tipo de transformador:" if language == "es" else "Transformer type:", estilo_normal),
            Paragraph(first_transformer_text, estilo_normal),
            Paragraph("OF:" if language == "es" else "OF:", estilo_normal),
            Paragraph(protocolData["of"], estilo_normal)
            ],
        ]
        tabla = Table(
            datos_tabla,
            colWidths=[
            ancho_util * 0.20,
            ancho_util * 0.20,
            ancho_util * 0.30,
            ancho_util * 0.30,
            ]
        )
        tabla.setStyle(TableStyle([
            ("BOX", (0, 1), (-1, 1), 0.5, colors.black),
            ("BOX", (0, 2), (-1, 2), 0.5, colors.black),
            ("BOX", (0, 3), (-1, 3), 0.5, colors.black),
            ("BOX", (0, 4), (-1, 4), 0.5, colors.black),
            ("BOX", (0, 5), (-1, 5), 0.5, colors.black),
            ("BOX", (0, 6), (-1, 6), 0.5, colors.black),
        ]))
        elementos.append(tabla)
        elementos.append(Spacer(1, 12))

        active_data = [
            [Paragraph("Activo" if language == "es" else "Active", estilo_bold)],
            [
            Paragraph("Ipn:" if language == "es" else "Ipn:", estilo_normal),
            Paragraph(protocolData["ipn"], estilo_normal),
            Paragraph("Pruebas dieléctricas" if language == "es" else "Dielectric tests", estilo_normal)
            ],
            [
            Paragraph("Isn:" if language == "es" else "Isn:", estilo_normal),
            Paragraph(protocolData["isn"], estilo_normal),
            Paragraph("Potencial aplicado:" if language == "es" else "Applied potential:", estilo_normal_centered)
            ],
            [
            Paragraph("Carga nominal:" if language == "es" else "Rated burden:", estilo_normal),
            Paragraph(protocolData["nominalBurden"], estilo_normal),
            Paragraph(protocolData["appliedPotential"], estilo_normal_centered)
            ],
            [
            Paragraph("Norma:" if language == "es" else "Standard:", estilo_normal),
            Paragraph(protocolData["rule"], estilo_normal)
            ],
            [
            Paragraph("Aplicación:" if language == "es" else "Application:", estilo_normal),
            Paragraph(f"{application_text} ({protocolData["firstCoreTerminals"]})", estilo_normal),
            Paragraph("Potencial inducido:" if language == "es" else "Induced potential:", estilo_normal_centered)
            ],
            [
            Paragraph("Clase:" if language == "es" else "Class:", estilo_normal),
            Paragraph(protocolData["class"], estilo_normal),
            Paragraph(protocolData["inducedPotential"], estilo_normal_centered)
            ],
            [
            Paragraph("Frecuencia:" if language == "es" else "Frequency:", estilo_normal),
            Paragraph(str(protocolData["frequency"]), estilo_normal),
            Paragraph(f"Identificación de Terminales: {protocolData["firstCoreTerminals"]}" if language == "es" else f"Tap setting: {protocolData["firstCoreTerminals"]}", estilo_normal_centered)
            ],
        ]
        active_table = Table(active_data, colWidths=[ancho_util * 0.2, ancho_util * 0.4, ancho_util * 0.4])
        active_table.setStyle(TableStyle([
            ("BOX", (0, 1), (1, -1), 0.5, colors.black),
            ("BOX", (-1, 2), (-1, -2), 0.5, colors.black),
            ("BOX", (2, 1), (-1, 1), 0.5, colors.black),
            ("BOX", (-1, -1), (-1, -1), 0.5, colors.black),
        ]))
        
        elementos.append(active_table)
        elementos.append(Spacer(1, 12))
        
        resistance_data = [
            [Paragraph("Resistencia del devanado secundario" if language == "es" else "Secondary widing resistance", estilo_bold)],
            [Paragraph("R-ref (75.0 °C):", estilo_normal), Paragraph(str(protocolData["resistance"]["rRef"]), estilo_normal), ""],
            [Paragraph("R-meas (25.0 °C):", estilo_normal), Paragraph(str(protocolData["resistance"]["rMeas"]), estilo_normal), ""],        ]
        resistance_table = Table(resistance_data, colWidths=[ancho_util * 0.4, ancho_util * 0.2, ancho_util * 0.4])
        resistance_table.setStyle(TableStyle([
            ("BOX", (0, 1), (1, 1), 0.5, colors.black),
            ("BOX", (0, 2), (1, 2), 0.5, colors.black),
        ]))
        
        
        elementos.append(resistance_table)
        elementos.append(Spacer(1, 12))

        excitation_data = [
            [Paragraph("Excitación" if language == "es" else "Excitation", estilo_bold)],
            [Paragraph(f"V-kn: {protocolData["excitation"]["vkn"]}", estilo_normal), Paragraph(f"I-kn: {protocolData["excitation"]["ikn"]}", estilo_normal), Paragraph(f"FS: {protocolData["excitation"]["fs"]}", estilo_normal), ""],        ]
        excitation_table = Table(excitation_data, colWidths=[ancho_util * 0.2, ancho_util * 0.2, ancho_util * 0.2, ancho_util * 0.4])
        excitation_table.setStyle(TableStyle([
            ("BOX", (0, 1), (0, 1), 0.5, colors.black),
            ("BOX", (1, 1), (1, 1), 0.5, colors.black),
            ("BOX", (2, 1), (2, 1), 0.5, colors.black),
        ]))
        
        elementos.append(excitation_table)
        elementos.append(Spacer(1, 12))
        
        # Determinar texto de polaridad según valor y idioma 
        polarity_value = protocolData["ratio"]["polarity"]
        if language == "es":
            polarity_text = "Correcto" if polarity_value == 1 else "Incorrecto"
        else:
            polarity_text = "Correct" if polarity_value == 1 else "Incorrect"

        ratio_data = [
            [Paragraph("Relación" if language == "es" else "Ratio", estilo_bold)],
            [
            Paragraph("Relación de transformación:" if language == "es" else "N:", estilo_normal),
            Paragraph(protocolData["ratio"]["ncore"], estilo_normal_right),
            Paragraph("Resultados en carga nominal:" if language == "es" else "Results at rated burden:", estilo_normal)
            ],
            [
            Paragraph("£t:" if language == "es" else "£t:", estilo_normal),
            Paragraph(protocolData["ratio"]["turnsError"], estilo_normal_right),
            Paragraph("Relación:" if language == "es" else "Ratio:", estilo_normal),
            "",
            "",
            Paragraph(protocolData["ratio"]["fileRatio"], estilo_normal)
            ],
            [
            Paragraph("Polaridad:" if language == "es" else "Polarity:", estilo_normal),
            Paragraph(polarity_text, estilo_normal_right),
            Paragraph("£:" if language == "es" else "£:", estilo_normal),
            Paragraph(protocolData["ratio"]["deviation"], estilo_normal_right),
            Paragraph("fase:" if language == "es" else "phase:", estilo_normal),
            Paragraph(protocolData["ratio"]["phase"], estilo_normal)
            ],
            [
            "",
            "",
            Paragraph("£C:" if language == "es" else "£C:", estilo_normal),
            Paragraph(protocolData["ratio"]["compositeError"], estilo_normal_right),
            "",
            ""
            ],
        ]
        ratio_table = Table(ratio_data, colWidths=[ancho_util * 0.25, ancho_util * 0.1, ancho_util * 0.25, ancho_util * 0.1, ancho_util * 0.1, ancho_util * 0.2])
        ratio_table.setStyle(TableStyle([
            ("BOX", (0, 1), (1, -1), 0.5, colors.black),
            ("BOX", (2, 1), (-1, 1), 0.5, colors.black),
            ("BOX", (2, 2), (-1, -1), 0.5, colors.black),
        ]))
        
        elementos.append(ratio_table)
        elementos.append(PageBreak())
        
        first_chart_object_first = protocolData["relationChartData"]
        first_chart_data_first = []
                
        first_chart_data_headers_first = []
        first_chart_data_relations_first = []
        first_chart_data_points_first = []
        
        for pointsLenght in first_chart_object_first[0]["points"]:
            first_chart_data_points_first.append([])
        
        for obj in first_chart_object_first:
            first_chart_data_headers_first.extend([obj["line"], ""])
            first_chart_data_relations_first.extend([obj["relation"], ""])
            
            for pointValues in obj["points"]:
                point_index = obj["points"].index(pointValues)
                
                for point in pointValues:
                    first_chart_data_points_first[point_index].append(f"{point:.2f}")
                    
                    
        first_chart_data_first_half_len = len(first_chart_data_headers_first) // 2
        new_row = []
        for header in first_chart_data_headers_first[:first_chart_data_first_half_len]:
            new_row.append("ICTef [A]")
            new_row.append("UCTef [V]")
        
        first_chart_data_first.append(first_chart_data_headers_first)
        first_chart_data_first.append(first_chart_data_relations_first)
        first_chart_data_first.append(new_row)
        
        for dataPoint in first_chart_data_points_first:
            first_chart_data_first.append(dataPoint)

        if len(first_chart_object_first) != 1:
            num_cols = len(first_chart_object_first) * 2        
        else:
            num_cols = 12
        
        col_width = ancho_util / num_cols
        first_chart_table_first = LongTable(first_chart_data_first, colWidths=[col_width] * num_cols)
        
        style = TableStyle([
            ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('FONTSIZE', (0,0), (-1,-1), 6),  # Aplica tamaño de fuente 8 a toda la tabla
            ('FONTSIZE', (0,2), (-1,2), 5),  # Aplica tamaño de fuente 8 a toda la tabla
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ])

        # Aplica SPAN para cada par
        for i in range(0, len(first_chart_data_headers_first), 2):
            style.add('SPAN', (i, 0), (i+1, 0))  # fila encabezado
            style.add('SPAN', (i, 1), (i+1, 1))  # fila relación, si quieres

        first_chart_table_first.setStyle(style)

        #elementos.append(first_chart_table_first)
        #elementos.append(Spacer(1, 12))
        
        #######GRAPHIC
        
        # 1️⃣ Crear gráfica log-log
        plt.figure(figsize=(7, 5))

        # Paleta de colores
        colorsPyplot = ['red', 'blue', 'green', 'orange', 'purple', 'brown', 'black', 'pink', 'gray', 'cyan']

        for idx, serie in enumerate(first_chart_object_first):
            # Extraer puntos
            points = np.array(serie["points"])
            x = points[:, 0]
            y = points[:, 1]

            # Etiqueta usando 'line' y 'relation'
            label = f"{serie['line']} ({serie['relation']})"

            plt.loglog(x, y, label=label, color=colorsPyplot[idx % len(colorsPyplot)])

            from matplotlib.ticker import ScalarFormatter
            
            ax = plt.gca()
            ax.xaxis.set_major_formatter(ScalarFormatter())
            ax.yaxis.set_major_formatter(ScalarFormatter())

        # Configurar gráfica
        plt.title("Curva de excitación" if language == "es" else "Excitation curve", fontsize=10, weight='bold')
        plt.xlabel("Corriente de excitación secundaria Ie [A]" if language == "es" else "Secondary excitation current Ie [A]")
        plt.ylabel("Voltaje de excitación [V]" if language == "es" else "Excitation voltage [V]")
        plt.grid(True, which="major", linestyle=":", linewidth=0.3, color="gray")
        plt.legend(fontsize=6)
        plt.tight_layout()

        # 2️⃣ Guardar como SVG
        plt.tight_layout()
        first_chart_uuid_first = f"{uuid.uuid4()}.svg"
        plt.savefig(first_chart_uuid_first, format="svg")
        plt.close()

        # 3️⃣ Cargar SVG con svglib
        drawing = svg2rlg(first_chart_uuid_first)

        # (Opcional) Ajustar tamaño en PDF:
        scale_factor = 0.7
        drawing.scale(scale_factor, scale_factor)
        drawing.width *= scale_factor
        drawing.height *= scale_factor
        
        # Tabla de la primera gráfica 
        
        first_chart_showing_first = []
        
        if len(first_chart_object_first) == 1:
            # Mostrar uno al lado del otro (1 fila, 2 columnas)
            first_chart_showing_first = [
                [first_chart_table_first, drawing]
            ]
            col_widths = [100, 420]  # Ajusta según el espacio A4 (aprox 520 pt usable)
        else:
            # Mostrar uno arriba del otro (2 filas, 1 columna)
            first_chart_showing_first = [
                [first_chart_table_first],
                [drawing]
            ]
            col_widths = [520]  # ancho total en una sola columna

        # Generar la tabla
        first_chart_table_showing_first = LongTable(first_chart_showing_first, colWidths=col_widths)

        first_chart_table_showing_first.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("ALIGN", (-1,-1), (-1,-1), "LEFT"),
        ]))

        # Insertar en ReportLab
        points_length = protocolData["relationChartData"][0]["points"]
        
        if len(first_chart_object_first) == 1:
            elementos.append(first_chart_table_showing_first)
            elementos.append(Spacer(1, 12))

        else:
            elementos.append(first_chart_table_first)
            # if len(points_length) > 14 and len(points_length) < 35:
            #     elementos.append(PageBreak())
            
            first_drawing_table = Table([[drawing]], colWidths=[ancho_util])
            first_drawing_table.setStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ])
            
            elementos.append(Spacer(1, 12))
            elementos.append(first_drawing_table)
            elementos.append(Spacer(1, 12))
        
        if os.path.exists(first_chart_uuid_first):
            os.remove(first_chart_uuid_first)
        
        # Segunda tabla
        
        second_chart_legend_first = protocolData["ratioErrorLegend"]
        second_chart_series_first = protocolData["ratioErrorSeries"]
                
        second_chart_table_data_first = [
            [Paragraph("Error de relación de corriente % a % de corriente nominal en carga nominal" if language == "es" else "Current ratio error in % at % of rated current", estilo_bold)],
            [Paragraph(f"{protocolData["firstCoreTerminals"]} ({protocolData["ratio"]["fileRatio"]})", estilo_normal_centered)]
        ]
        
        second_chart_table_third_row_first = [
            Paragraph("VA/ Cos phi", estilo_normal_centered),
        ]
        
        for percent in second_chart_series_first[0]["data"]:
            second_chart_table_third_row_first.append(Paragraph(f"{percent[0]}%", estilo_normal_centered))
        
        second_chart_table_third_row_first.append(Paragraph("Designación" if language == "es" else "Designation", estilo_normal_centered))
        second_chart_table_data_first.append(second_chart_table_third_row_first)
        
        for serie in second_chart_series_first:
            serie_index = second_chart_series_first.index(serie)
            row_to_append = [Paragraph(second_chart_legend_first[serie_index]["power"], estilo_normal_centered)]
            
            for serie_data in serie["data"]:
                row_to_append.append(Paragraph(str(serie_data[1]), estilo_normal_centered))
            
            row_to_append.append(Paragraph(second_chart_legend_first[serie_index]["designation"], estilo_normal_centered))
            second_chart_table_data_first.append(row_to_append)

        second_chart_table_widths_first = [ancho_util * 0.15]
        
        for width in second_chart_series_first[0]["data"]:
            second_chart_table_widths_first.append(ancho_util * 0.65 / len(second_chart_series_first[0]["data"]))
        second_chart_table_widths_first.append(ancho_util * 0.2)
        
        percentCellWidth = 0.6 / len(second_chart_series_first[0]["data"])
        second_chart_table_showing_first = Table(second_chart_table_data_first, colWidths=second_chart_table_widths_first)

        second_chart_table_showing_first.setStyle(TableStyle([
            ("SPAN", (0, 0), (-1, 0)),  # Encabezado
            ("SPAN", (0, 1), (-1, 1)),  # Segunda
            ("GRID", (0, 1), (-1, -1), 0.5, colors.black),
        ]))

        elementos.append(KeepTogether(second_chart_table_showing_first))
        elementos.append(Spacer(1, 12))
        
        # Segunda gráfica
        
        plt.figure(figsize=(7, 4))

        for idx, serie in enumerate(second_chart_series_first):
            name = serie["name"].strip()
            data = serie["data"]
            
            # Filtrar datos válidos (ignorando null)
            x = [point[0] for point in data if point[0] is not None and point[1] is not None]
            y = [point[1] for point in data if point[0] is not None and point[1] is not None]
            
            plt.plot(x, y, marker='o', label=name, color=colorsPyplot[idx % len(colorsPyplot)])

        # Configurar etiquetas y grilla
        plt.xlabel("Porcentaje de corriente secundaria I/Ipn [%]" if language == "es" else "Secondary current percentage I/Ipn [%]")
        plt.ylabel("Error de relación de corriente [%]" if language == "es" else "Current ratio error [%]")
        plt.title("Error de relación de corriente % a % de corriente nominal en carga nominal" if language == "es" else "Current ratio error in % at % of rated current", fontsize=10, weight='bold')

        # Grilla sutil
        plt.grid(True, which="major", linestyle="--", linewidth=0.5, alpha=0.7)

        # Leyenda
        plt.legend(fontsize=6, loc='best')

        # Ajustar límites de X si deseas consistencia visual
        plt.xlim(0, 120)
        # Ajustar límites de Y si deseas consistencia visual
        # plt.ylim(-0.8, 0.05)

        plt.tight_layout()

        # Guardar como SVG
        second_chart_uuid_first = f"{uuid.uuid4()}.svg"
        plt.savefig(second_chart_uuid_first, format="svg")
        plt.close()

        # Insertar en PDF
        drawing = svg2rlg(second_chart_uuid_first)

        # Ajustar para A4
        page_width, page_height = A4
        margen = 36
        usable_width = page_width - 2 * margen
        usable_height = 300

        scale_factor = usable_width / drawing.width
        if drawing.height * scale_factor > usable_height:
            scale_factor = usable_height / drawing.height

        drawing.width *= scale_factor
        drawing.height *= scale_factor
        drawing.scale(scale_factor, scale_factor)

        # Centrar usando Table
        table = Table([[drawing]], colWidths=[usable_width])
        table.setStyle([
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ])

        elementos.append(table)
        elementos.append(Spacer(1, 12))

        # Tercera tabla
        
        third_chart_legend_first = protocolData["phaseLegend"]
        third_chart_series_first = protocolData["phaseSeries"]
        
        third_chart_table_data_first = [
            [Paragraph("Fase en min a % de la corriente nominal en carga nominal" if language == "es" else "Phase in min at % of rated current", estilo_bold)],
            [Paragraph(f"{protocolData["firstCoreTerminals"]} ({protocolData["ratio"]["fileRatio"]})", estilo_normal_centered)]
        ]
        
        third_chart_table_third_row_first = [
            Paragraph("VA/ Cos phi", estilo_normal_centered),
        ]
        
        for percent in third_chart_series_first[0]["data"]:
            third_chart_table_third_row_first.append(Paragraph(f"{percent[0]}%", estilo_normal_centered))
        
        third_chart_table_third_row_first.append(Paragraph("Designación" if language == "es" else "Designation", estilo_normal_centered))
        third_chart_table_data_first.append(third_chart_table_third_row_first)

        for serie in third_chart_series_first:
            serie_index = third_chart_series_first.index(serie)
            row_to_append = [Paragraph(third_chart_legend_first[serie_index]["power"], estilo_normal_centered)]
            
            for serie_data in serie["data"]:
                row_to_append.append(Paragraph(str(serie_data[1]), estilo_normal_centered))
            
            row_to_append.append(Paragraph(third_chart_legend_first[serie_index]["designation"], estilo_normal_centered))
            third_chart_table_data_first.append(row_to_append)

        third_chart_table_widths_first = [ancho_util * 0.15]
        
        for width in third_chart_series_first[0]["data"]:
            third_chart_table_widths_first.append(ancho_util * 0.65 / len(third_chart_series_first[0]["data"]))
        third_chart_table_widths_first.append(ancho_util * 0.2)
        
        percentCellWidth = 0.6 / len(third_chart_series_first[0]["data"])
        third_chart_table_showing_first = Table(third_chart_table_data_first, colWidths=third_chart_table_widths_first)

        third_chart_table_showing_first.setStyle(TableStyle([
            ("SPAN", (0, 0), (-1, 0)),  # Encabezado
            ("SPAN", (0, 1), (-1, 1)),  # Segunda
            ("GRID", (0, 1), (-1, -1), 0.5, colors.black),
        ]))

        elementos.append(KeepTogether(third_chart_table_showing_first))
        elementos.append(Spacer(1, 12))
        
        if os.path.exists(second_chart_uuid_first):
            os.remove(second_chart_uuid_first)
            
        # Segunda gráfica
        
        plt.figure(figsize=(7, 4))

        for idx, serie in enumerate(third_chart_series_first):
            name = serie["name"].strip()
            data = serie["data"]
            
            # Filtrar datos válidos (ignorando null)
            x = [point[0] for point in data if point[0] is not None and point[1] is not None]
            y = [point[1] for point in data if point[0] is not None and point[1] is not None]
            
            plt.plot(x, y, marker='o', label=name, color=colorsPyplot[idx % len(colorsPyplot)])

        # Configurar etiquetas y grilla
        plt.xlabel("Porcentaje de corriente secundaria I/Ipn [%]" if language == "es" else "Secondary current percentage I/Ipn [%]")
        plt.ylabel("Fase [min]" if language == "es" else "Phase [min]")
        plt.title("Fase en min a % de la corriente nominal" if language == "es" else "Phase in min at % of rated current", fontsize=10, weight='bold')

        # Grilla sutil
        plt.grid(True, which="major", linestyle="--", linewidth=0.5, alpha=0.7)

        # Leyenda
        plt.legend(fontsize=6, loc='best')

        # Ajustar límites de X si deseas consistencia visual
        plt.xlim(0, 120)
        # Ajustar límites de Y si deseas consistencia visual
        # plt.ylim(-0.8, 0.05)

        plt.tight_layout()

        # Guardar como SVG
        third_chart_uuid_first = f"{uuid.uuid4()}.svg"
        plt.savefig(third_chart_uuid_first, format="svg")
        plt.close()

        # Insertar en PDF
        drawing = svg2rlg(third_chart_uuid_first)

        # Ajustar para A4
        page_width, page_height = A4
        margen = 36
        usable_width = page_width - 2 * margen
        usable_height = 300

        scale_factor = usable_width / drawing.width
        if drawing.height * scale_factor > usable_height:
            scale_factor = usable_height / drawing.height

        drawing.width *= scale_factor
        drawing.height *= scale_factor
        drawing.scale(scale_factor, scale_factor)

        # Centrar usando Table
        table = Table([[drawing]], colWidths=[usable_width])
        table.setStyle([
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ])

        elementos.append(table)
        elementos.append(Spacer(1, 12))        
        
        if protocolData["secondCore"]:
            elementos.append(PageBreak())
            
            second_transformer_value = int(protocolData["secondCore"]["transformerType"])
            second_transformer_text = next((transformer["nameSpanish"] if language == "es" else transformer["nameEnglish"] for transformer in transformers if transformer["id"] == second_transformer_value), "Desconocido")            

            evaluation_value_second = protocolData["secondCore"]["generalEvaluation"]
            if language == "es":
                evaluation_text_second = "CORRECTO" if evaluation_value_second == 1 else "INCORRECTO"
            else:
                evaluation_text_second = "CORRECT" if evaluation_value_second == 1 else "INCORRECT"
                
            datos_tabla_second = [
                [
                Paragraph("Dispositivo de prueba" if language == "es" else "Test device", estilo_bold), "",
                Paragraph("Fecha/hora:" if language == "es" else "Date/time:", estilo_bold),
                Paragraph(protocolData["secondCore"]["testDevice"]["dateTime"], estilo_bold)
                ],
                [
                Paragraph("Dispositivo de prueba:" if language == "es" else "Test device:", estilo_normal),
                Paragraph(protocolData["secondCore"]["testDevice"]["type"], estilo_normal),
                Paragraph("No serie del dispositivo:" if language == "es" else "Device serial number:", estilo_normal),
                Paragraph(protocolData["secondCore"]["testDevice"]["serialNumber"], estilo_normal)
                ],
                [
                Paragraph("Modelo de TC:" if language == "es" else "CT model:", estilo_normal),
                Paragraph(protocolData["secondCore"]["tcModel"], estilo_normal),
                Paragraph("No. de Serie:" if language == "es" else "Serial number:", estilo_normal),
                Paragraph(protocolData["secondCore"]["deviceSerial"], estilo_normal)
                ],
                [
                Paragraph("Orden de Compra:" if language == "es" else "Purchase order:", estilo_normal),
                Paragraph(protocolData["secondCore"]["purchaseOrder"], estilo_normal),
                Paragraph("Factor térmico continuo 30°C AMB:" if language == "es" else "Continuous thermal factor 30°C AMB:", estilo_normal),
                Paragraph(protocolData["secondCore"]["thermalFactor"], estilo_normal)
                ],
                [
                Paragraph("Evaluación general:" if language == "es" else "General evaluation:", estilo_normal),
                Paragraph(evaluation_text_second, estilo_normal),
                Paragraph("Cliente:" if language == "es" else "Customer:", estilo_normal),
                Paragraph(protocolData["secondCore"]["customer"], estilo_normal)
                ],
                [
                Paragraph("Cantidad:" if language == "es" else "Quantity:", estilo_normal),
                Paragraph(f"{protocolData["secondCore"]["quantity"]} {"Pieza" if language == "es" else "Piece"}{"s" if int(protocolData["secondCore"]["quantity"]) > 1 else ""}", estilo_normal),
                Paragraph("Clase de aislamiento:" if language == "es" else "Insulation class:", estilo_normal),
                Paragraph(str(protocolData["secondCore"]["insulationClass"]), estilo_normal)
                ],
                [
                Paragraph("Tipo de transformador:" if language == "es" else "Transformer type:", estilo_normal),
                Paragraph(second_transformer_text, estilo_normal),
                Paragraph("OF:" if language == "es" else "OF:", estilo_normal),
                Paragraph(protocolData["secondCore"]["of"], estilo_normal)
                ],
            ]
            tabla_second = Table(
                datos_tabla_second,
                colWidths=[
                ancho_util * 0.20,
                ancho_util * 0.20,
                ancho_util * 0.30,
                ancho_util * 0.30,
                ]
            )
            tabla_second.setStyle(TableStyle([
                ("BOX", (0, 1), (-1, 1), 0.5, colors.black),
                ("BOX", (0, 2), (-1, 2), 0.5, colors.black),
                ("BOX", (0, 3), (-1, 3), 0.5, colors.black),
                ("BOX", (0, 4), (-1, 4), 0.5, colors.black),
                ("BOX", (0, 5), (-1, 5), 0.5, colors.black),
                ("BOX", (0, 6), (-1, 6), 0.5, colors.black),
            ]))
            elementos.append(tabla_second)
            elementos.append(Spacer(1, 12))

            active_data_second = [
                [Paragraph("Activo" if language == "es" else "Active", estilo_bold)],
                [
                Paragraph("Ipn:" if language == "es" else "Ipn:", estilo_normal),
                Paragraph(protocolData["secondCore"]["ipn"], estilo_normal),
                Paragraph("Pruebas dieléctricas" if language == "es" else "Dielectric tests", estilo_normal)
                ],
                [
                Paragraph("Isn:" if language == "es" else "Isn:", estilo_normal),
                Paragraph(protocolData["secondCore"]["isn"], estilo_normal),
                Paragraph("Potencial aplicado:" if language == "es" else "Applied potential:", estilo_normal_centered)
                ],
                [
                Paragraph("Carga nominal:" if language == "es" else "Rated burden:", estilo_normal),
                Paragraph(protocolData["secondCore"]["nominalBurden"], estilo_normal),
                Paragraph(protocolData["secondCore"]["appliedPotential"], estilo_normal_centered)
                ],
                [
                Paragraph("Norma:" if language == "es" else "Standard:", estilo_normal),
                Paragraph(protocolData["secondCore"]["rule"], estilo_normal)
                ],
                [
                Paragraph("Aplicación:" if language == "es" else "Application:", estilo_normal),
                Paragraph(f"{application_text} ({protocolData["secondCore"]["secondCoreTerminals"]})", estilo_normal),
                Paragraph("Potencial inducido:" if language == "es" else "Induced potential:", estilo_normal_centered)
                ],
                [
                Paragraph("Clase:" if language == "es" else "Class:", estilo_normal),
                Paragraph(protocolData["secondCore"]["class"], estilo_normal),
                Paragraph(protocolData["secondCore"]["inducedPotential"], estilo_normal_centered)
                ],
                [
                Paragraph("Frecuencia:" if language == "es" else "Frequency:", estilo_normal),
                Paragraph(str(protocolData["secondCore"]["frequency"]), estilo_normal),
                Paragraph(f"Identificación de Terminales: {protocolData["secondCore"]["secondCoreTerminals"]}" if language == "es" else f"Tap setting: {protocolData["secondCore"]["secondCoreTerminals"]}", estilo_normal_centered)
                ],
            ]
            active_table_second = Table(active_data_second, colWidths=[ancho_util * 0.2, ancho_util * 0.4, ancho_util * 0.4])
            active_table_second.setStyle(TableStyle([
                ("BOX", (0, 1), (1, -1), 0.5, colors.black),
                ("BOX", (-1, 2), (-1, -2), 0.5, colors.black),
                ("BOX", (2, 1), (-1, 1), 0.5, colors.black),
                ("BOX", (-1, -1), (-1, -1), 0.5, colors.black),
            ]))
            
            elementos.append(active_table_second)
            elementos.append(Spacer(1, 12))
            
            resistance_data_second = [
                [Paragraph("Resistencia del devanado secundario" if language == "es" else "Secondary widing resistance", estilo_bold)],
                [Paragraph("R-ref (75.0 °C):", estilo_normal), Paragraph(str(protocolData["secondCore"]["resistance"]["rRef"]), estilo_normal), ""],
                [Paragraph("R-meas (25.0 °C):", estilo_normal), Paragraph(str(protocolData["secondCore"]["resistance"]["rMeas"]), estilo_normal), ""],        ]
            resistance_table_second = Table(resistance_data_second, colWidths=[ancho_util * 0.4, ancho_util * 0.2, ancho_util * 0.4])
            resistance_table_second.setStyle(TableStyle([
                ("BOX", (0, 1), (1, 1), 0.5, colors.black),
                ("BOX", (0, 2), (1, 2), 0.5, colors.black),
            ]))
            
            
            elementos.append(resistance_table_second)
            elementos.append(Spacer(1, 12))

            excitation_data_second = [
                [Paragraph("Excitación" if language == "es" else "Excitation", estilo_bold)],
                [Paragraph(f"V-kn: {protocolData["secondCore"]["excitation"]["vkn"]}", estilo_normal), Paragraph(f"I-kn: {protocolData["secondCore"]["excitation"]["ikn"]}", estilo_normal), Paragraph(f"FS: {protocolData["secondCore"]["excitation"]["fs"]}", estilo_normal), ""],        ]
            excitation_table_second = Table(excitation_data_second, colWidths=[ancho_util * 0.2, ancho_util * 0.2, ancho_util * 0.2, ancho_util * 0.4])
            excitation_table_second.setStyle(TableStyle([
                ("BOX", (0, 1), (0, 1), 0.5, colors.black),
                ("BOX", (1, 1), (1, 1), 0.5, colors.black),
                ("BOX", (2, 1), (2, 1), 0.5, colors.black),
            ]))
            
            elementos.append(excitation_table_second)
            elementos.append(Spacer(1, 12))
            
            # Determinar texto de polaridad según valor y idioma 
            polarity_value_second = protocolData["secondCore"]["ratio"]["polarity"]
            if language == "es":
                polarity_text_second = "Correcto" if polarity_value_second == 1 else "Incorrecto"
            else:
                polarity_text_second = "Correct" if polarity_value_second == 1 else "Incorrect"

            ratio_data_second = [
                [Paragraph("Relación" if language == "es" else "Ratio", estilo_bold)],
                [
                Paragraph("Relación de transformación:" if language == "es" else "N:", estilo_normal),
                Paragraph(protocolData["secondCore"]["ratio"]["ncore"], estilo_normal_right),
                Paragraph("Resultados en carga nominal:" if language == "es" else "Results at rated burden:", estilo_normal)
                ],
                [
                Paragraph("£t:" if language == "es" else "£t:", estilo_normal),
                Paragraph(protocolData["secondCore"]["ratio"]["turnsError"], estilo_normal_right),
                Paragraph("Relación:" if language == "es" else "Ratio:", estilo_normal),
                "",
                "",
                Paragraph(protocolData["secondCore"]["ratio"]["fileRatio"], estilo_normal)
                ],
                [
                Paragraph("Polaridad:" if language == "es" else "Polarity:", estilo_normal),
                Paragraph(polarity_text_second, estilo_normal_right),
                Paragraph("£:" if language == "es" else "£:", estilo_normal),
                Paragraph(protocolData["secondCore"]["ratio"]["deviation"], estilo_normal_right),
                Paragraph("fase:" if language == "es" else "phase:", estilo_normal),
                Paragraph(protocolData["secondCore"]["ratio"]["phase"], estilo_normal)
                ],
                [
                "",
                "",
                Paragraph("£C:" if language == "es" else "£C:", estilo_normal),
                Paragraph(protocolData["secondCore"]["ratio"]["compositeError"], estilo_normal_right),
                "",
                ""
                ],
            ]
            ratio_table_second = Table(ratio_data_second, colWidths=[ancho_util * 0.25, ancho_util * 0.1, ancho_util * 0.25, ancho_util * 0.1, ancho_util * 0.1, ancho_util * 0.2])
            ratio_table_second.setStyle(TableStyle([
                ("BOX", (0, 1), (1, -1), 0.5, colors.black),
                ("BOX", (2, 1), (-1, 1), 0.5, colors.black),
                ("BOX", (2, 2), (-1, -1), 0.5, colors.black),
            ]))
            
            elementos.append(ratio_table_second)
            elementos.append(PageBreak())
            
            first_chart_object_second = protocolData["secondCore"]["relationChartData"]
            first_chart_data_second = []
                    
            first_chart_data_headers_second = []
            first_chart_data_relations_second = []
            first_chart_data_points_second = []
            
            for pointsLenght in first_chart_object_second[0]["points"]:
                first_chart_data_points_first.append([])
            
            for obj in first_chart_object_second:
                first_chart_data_headers_second.extend([obj["line"], ""])
                first_chart_data_relations_second.extend([obj["relation"], ""])
                
                for pointValues in obj["points"]:
                    point_index = obj["points"].index(pointValues)
                    
                    for point in pointValues:
                        first_chart_data_points_second[point_index].append(f"{point:.2f}")
                        
                        
            first_chart_data_second_half_len = len(first_chart_data_headers_second) // 2
            new_row = []
            for header in first_chart_data_headers_second[:first_chart_data_second_half_len]:
                new_row.append("ICTef [A]")
                new_row.append("UCTef [V]")
            
            first_chart_data_second.append(first_chart_data_headers_second)
            first_chart_data_second.append(first_chart_data_relations_second)
            first_chart_data_second.append(new_row)
            
            for dataPoint in first_chart_data_points_second:
                first_chart_data_second.append(dataPoint)

            if len(first_chart_object_second) != 1:
                num_cols = len(first_chart_object_second) * 2        
            else:
                num_cols = 12
            
            col_width = ancho_util / num_cols
            first_chart_table_second = LongTable(first_chart_data_second, colWidths=[col_width] * num_cols)
            
            style = TableStyle([
                ('GRID', (0,0), (-1,-1), 1, colors.black),
                ('FONTSIZE', (0,0), (-1,-1), 6),  # Aplica tamaño de fuente 8 a toda la tabla
                ('FONTSIZE', (0,2), (-1,2), 5),  # Aplica tamaño de fuente 8 a toda la tabla
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ])

            # Aplica SPAN para cada par
            for i in range(0, len(first_chart_data_headers_second), 2):
                style.add('SPAN', (i, 0), (i+1, 0))  # fila encabezado
                style.add('SPAN', (i, 1), (i+1, 1))  # fila relación, si quieres

            first_chart_table_second.setStyle(style)

            #elementos.append(first_chart_table_first)
            #elementos.append(Spacer(1, 12))
            
            #######GRAPHIC
            
            # 1️⃣ Crear gráfica log-log
            plt.figure(figsize=(7, 5))

            # Paleta de colores
            colorsPyplot = ['red', 'blue', 'green', 'orange', 'purple', 'brown', 'black', 'pink', 'gray', 'cyan']

            for idx, serie in enumerate(first_chart_object_second):
                # Extraer puntos
                points = np.array(serie["points"])
                x = points[:, 0]
                y = points[:, 1]

                # Etiqueta usando 'line' y 'relation'
                label = f"{serie['line']} ({serie['relation']})"

                plt.loglog(x, y, label=label, color=colorsPyplot[idx % len(colorsPyplot)])

                from matplotlib.ticker import ScalarFormatter
                
                ax = plt.gca()
                ax.xaxis.set_major_formatter(ScalarFormatter())
                ax.yaxis.set_major_formatter(ScalarFormatter())

            # Configurar gráfica
            plt.title("Curva de excitación" if language == "es" else "Excitation curve", fontsize=10, weight='bold')
            plt.xlabel("Corriente de excitación secundaria Ie [A]" if language == "es" else "Secondary excitation current Ie [A]")
            plt.ylabel("Voltaje de excitación [V]" if language == "es" else "Excitation voltage [V]")
            plt.grid(True, which="major", linestyle=":", linewidth=0.3, color="gray")
            plt.legend(fontsize=6)
            plt.tight_layout()

            # 2️⃣ Guardar como SVG
            plt.tight_layout()
            first_chart_uuid_second = f"{uuid.uuid4()}.svg"
            plt.savefig(first_chart_uuid_second, format="svg")
            plt.close()

            # 3️⃣ Cargar SVG con svglib
            drawing = svg2rlg(first_chart_uuid_second)

            # (Opcional) Ajustar tamaño en PDF:
            scale_factor = 0.7
            drawing.scale(scale_factor, scale_factor)
            drawing.width *= scale_factor
            drawing.height *= scale_factor
            
            # Tabla de la primera gráfica 
            
            first_chart_showing_second = []
            
            if len(first_chart_object_second) == 1:
                # Mostrar uno al lado del otro (1 fila, 2 columnas)
                first_chart_showing_second = [
                    [first_chart_table_second, drawing]
                ]
                col_widths = [100, 420]  # Ajusta según el espacio A4 (aprox 520 pt usable)
            else:
                # Mostrar uno arriba del otro (2 filas, 1 columna)
                first_chart_showing_second = [
                    [first_chart_table_second],
                    [drawing]
                ]
                col_widths = [520]  # ancho total en una sola columna

            # Generar la tabla
            first_chart_table_showing_second = LongTable(first_chart_showing_second, colWidths=col_widths)

            first_chart_table_showing_second.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0,0), (-1,-1), "CENTER"),
                ("ALIGN", (-1,-1), (-1,-1), "LEFT"),
            ]))

            # Insertar en ReportLab
            points_length = protocolData["secondCore"]["relationChartData"][0]["points"]
            
            if len(first_chart_object_second) == 1:
                elementos.append(first_chart_table_showing_second)
                elementos.append(Spacer(1, 12))

            else:
                elementos.append(first_chart_table_second)
                # if len(points_length) > 14 and len(points_length) < 35:
                #     elementos.append(PageBreak())
                
                first_drawing_table_second = Table([[drawing]], colWidths=[ancho_util])
                first_drawing_table_second.setStyle([
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ])
                
                elementos.append(Spacer(1, 12))
                elementos.append(first_drawing_table_second)
                elementos.append(Spacer(1, 12))
            
            if os.path.exists(first_chart_uuid_second):
                os.remove(first_chart_uuid_first)
            
            # Segunda tabla
            
            second_chart_legend_second = protocolData["secondCore"]["ratioErrorLegend"]
            second_chart_series_second = protocolData["secondCore"]["ratioErrorSeries"]
                    
            second_chart_table_data_second = [
                [Paragraph("Error de relación de corriente % a % de corriente nominal en carga nominal" if language == "es" else "Current ratio error in % at % of rated current", estilo_bold)],
                [Paragraph(f"{protocolData["secondCore"]["secondCoreTerminals"]} ({protocolData["secondCore"]["ratio"]["fileRatio"]})", estilo_normal_centered)]
            ]
            
            second_chart_table_third_row_second = [
                Paragraph("VA/ Cos phi", estilo_normal_centered),
            ]
            
            for percent in second_chart_series_second[0]["data"]:
                second_chart_table_third_row_second.append(Paragraph(f"{percent[0]}%", estilo_normal_centered))
            
            second_chart_table_third_row_second.append(Paragraph("Designación" if language == "es" else "Designation", estilo_normal_centered))
            second_chart_table_data_second.append(second_chart_table_third_row_second)
            
            for serie in second_chart_series_second:
                serie_index = second_chart_series_second.index(serie)
                row_to_append = [Paragraph(second_chart_legend_second[serie_index]["power"], estilo_normal_centered)]
                
                for serie_data in serie["data"]:
                    row_to_append.append(Paragraph(str(serie_data[1]), estilo_normal_centered))
                
                row_to_append.append(Paragraph(second_chart_legend_second[serie_index]["designation"], estilo_normal_centered))
                second_chart_table_data_second.append(row_to_append)

            second_chart_table_widths_second = [ancho_util * 0.15]
            
            for width in second_chart_series_second[0]["data"]:
                second_chart_table_widths_second.append(ancho_util * 0.65 / len(second_chart_series_second[0]["data"]))
            second_chart_table_widths_second.append(ancho_util * 0.2)
            
            percentCellWidth = 0.6 / len(second_chart_series_second[0]["data"])
            second_chart_table_showing_second = Table(second_chart_table_data_second, colWidths=second_chart_table_widths_second)

            second_chart_table_showing_second.setStyle(TableStyle([
                ("SPAN", (0, 0), (-1, 0)),  # Encabezado
                ("SPAN", (0, 1), (-1, 1)),  # Segunda
                ("GRID", (0, 1), (-1, -1), 0.5, colors.black),
            ]))

            elementos.append(KeepTogether(second_chart_table_showing_second))
            elementos.append(Spacer(1, 12))
            
            # Segunda gráfica
            
            plt.figure(figsize=(7, 4))

            for idx, serie in enumerate(second_chart_series_second):
                name = serie["name"].strip()
                data = serie["data"]
                
                # Filtrar datos válidos (ignorando null)
                x = [point[0] for point in data if point[0] is not None and point[1] is not None]
                y = [point[1] for point in data if point[0] is not None and point[1] is not None]
                
                plt.plot(x, y, marker='o', label=name, color=colorsPyplot[idx % len(colorsPyplot)])

            # Configurar etiquetas y grilla
            plt.xlabel("Porcentaje de corriente secundaria I/Ipn [%]" if language == "es" else "Secondary current percentage I/Ipn [%]")
            plt.ylabel("Error de relación de corriente [%]" if language == "es" else "Current ratio error [%]")
            plt.title("Error de relación de corriente % a % de corriente nominal en carga nominal" if language == "es" else "Current ratio error in % at % of rated current", fontsize=10, weight='bold')

            # Grilla sutil
            plt.grid(True, which="major", linestyle="--", linewidth=0.5, alpha=0.7)

            # Leyenda
            plt.legend(fontsize=6, loc='best')

            # Ajustar límites de X si deseas consistencia visual
            plt.xlim(0, 120)
            # Ajustar límites de Y si deseas consistencia visual
            # plt.ylim(-0.8, 0.05)

            plt.tight_layout()

            # Guardar como SVG
            second_chart_uuid_second = f"{uuid.uuid4()}.svg"
            plt.savefig(second_chart_uuid_second, format="svg")
            plt.close()

            # Insertar en PDF
            drawing = svg2rlg(second_chart_uuid_second)

            # Ajustar para A4
            page_width, page_height = A4
            margen = 36
            usable_width = page_width - 2 * margen
            usable_height = 300

            scale_factor = usable_width / drawing.width
            if drawing.height * scale_factor > usable_height:
                scale_factor = usable_height / drawing.height

            drawing.width *= scale_factor
            drawing.height *= scale_factor
            drawing.scale(scale_factor, scale_factor)

            # Centrar usando Table
            table = Table([[drawing]], colWidths=[usable_width])
            table.setStyle([
                ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ])

            elementos.append(table)
            elementos.append(Spacer(1, 12))

            # Tercera tabla
            
            third_chart_legend_second = protocolData["secondCore"]["phaseLegend"]
            third_chart_series_second = protocolData["secondCore"]["phaseSeries"]
            
            third_chart_table_data_second = [
                [Paragraph("Fase en min a % de la corriente nominal en carga nominal" if language == "es" else "Phase in min at % of rated current", estilo_bold)],
                [Paragraph(f"{protocolData["secondCore"]["secondCoreTerminals"]} ({protocolData["secondCore"]["ratio"]["fileRatio"]})", estilo_normal_centered)]
            ]
            
            third_chart_table_third_row_second = [
                Paragraph("VA/ Cos phi", estilo_normal_centered),
            ]
            
            for percent in third_chart_series_second[0]["data"]:
                third_chart_table_third_row_second.append(Paragraph(f"{percent[0]}%", estilo_normal_centered))
            
            third_chart_table_third_row_second.append(Paragraph("Designación" if language == "es" else "Designation", estilo_normal_centered))
            third_chart_table_data_second.append(third_chart_table_third_row_second)

            for serie in third_chart_series_second:
                serie_index = third_chart_series_second.index(serie)
                row_to_append = [Paragraph(third_chart_legend_second[serie_index]["power"], estilo_normal_centered)]
                
                for serie_data in serie["data"]:
                    row_to_append.append(Paragraph(str(serie_data[1]), estilo_normal_centered))
                
                row_to_append.append(Paragraph(third_chart_legend_second[serie_index]["designation"], estilo_normal_centered))
                third_chart_table_data_second.append(row_to_append)

            third_chart_table_widths_second = [ancho_util * 0.15]
            
            for width in third_chart_series_second[0]["data"]:
                third_chart_table_widths_second.append(ancho_util * 0.65 / len(third_chart_series_second[0]["data"]))
            third_chart_table_widths_second.append(ancho_util * 0.2)
            
            percentCellWidth = 0.6 / len(third_chart_series_second[0]["data"])
            third_chart_table_showing_second = Table(third_chart_table_data_second, colWidths=third_chart_table_widths_second)

            third_chart_table_showing_second.setStyle(TableStyle([
                ("SPAN", (0, 0), (-1, 0)),  # Encabezado
                ("SPAN", (0, 1), (-1, 1)),  # Segunda
                ("GRID", (0, 1), (-1, -1), 0.5, colors.black),
            ]))

            elementos.append(KeepTogether(third_chart_table_showing_second))
            elementos.append(Spacer(1, 12))
            
            if os.path.exists(second_chart_uuid_second):
                os.remove(second_chart_uuid_second)
                
            # Tercera gráfica
            
            plt.figure(figsize=(7, 4))

            for idx, serie in enumerate(third_chart_series_second):
                name = serie["name"].strip()
                data = serie["data"]
                
                # Filtrar datos válidos (ignorando null)
                x = [point[0] for point in data if point[0] is not None and point[1] is not None]
                y = [point[1] for point in data if point[0] is not None and point[1] is not None]
                
                plt.plot(x, y, marker='o', label=name, color=colorsPyplot[idx % len(colorsPyplot)])

            # Configurar etiquetas y grilla
            plt.xlabel("Porcentaje de corriente secundaria I/Ipn [%]" if language == "es" else "Secondary current percentage I/Ipn [%]")
            plt.ylabel("Fase [min]" if language == "es" else "Phase [min]")
            plt.title("Fase en min a % de la corriente nominal" if language == "es" else "Phase in min at % of rated current", fontsize=10, weight='bold')

            # Grilla sutil
            plt.grid(True, which="major", linestyle="--", linewidth=0.5, alpha=0.7)

            # Leyenda
            plt.legend(fontsize=6, loc='best')

            # Ajustar límites de X si deseas consistencia visual
            plt.xlim(0, 120)
            # Ajustar límites de Y si deseas consistencia visual
            # plt.ylim(-0.8, 0.05)

            plt.tight_layout()

            # Guardar como SVG
            third_chart_uuid_second = f"{uuid.uuid4()}.svg"
            plt.savefig(third_chart_uuid_second, format="svg")
            plt.close()

            # Insertar en PDF
            drawing = svg2rlg(third_chart_uuid_second)

            # Ajustar para A4
            page_width, page_height = A4
            margen = 36
            usable_width = page_width - 2 * margen
            usable_height = 300

            scale_factor = usable_width / drawing.width
            if drawing.height * scale_factor > usable_height:
                scale_factor = usable_height / drawing.height

            drawing.width *= scale_factor
            drawing.height *= scale_factor
            drawing.scale(scale_factor, scale_factor)

            # Centrar usando Table
            table = Table([[drawing]], colWidths=[usable_width])
            table.setStyle([
                ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ])

            elementos.append(table)
            elementos.append(Spacer(1, 12))        

        if protocolData["thirdCore"]:
            elementos.append(PageBreak())
            
            third_transformer_value = int(protocolData["thirdCore"]["transformerType"])
            third_transformer_text = next((transformer["nameSpanish"] if language == "es" else transformer["nameEnglish"] for transformer in transformers if transformer["id"] == third_transformer_value), "Desconocido")            

            print('tercer trans', third_transformer_value)

            evaluation_value_third = protocolData["thirdCore"]["generalEvaluation"]
            if language == "es":
                evaluation_text_third = "CORRECTO" if evaluation_value_third == 1 else "INCORRECTO"
            else:
                evaluation_text_third = "CORRECT" if evaluation_value_third == 1 else "INCORRECT"
                
            datos_tabla_third = [
                [
                Paragraph("Dispositivo de prueba" if language == "es" else "Test device", estilo_bold), "",
                Paragraph("Fecha/hora:" if language == "es" else "Date/time:", estilo_bold),
                Paragraph(protocolData["thirdCore"]["testDevice"]["dateTime"], estilo_bold)
                ],
                [
                Paragraph("Dispositivo de prueba:" if language == "es" else "Test device:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["testDevice"]["type"], estilo_normal),
                Paragraph("No serie del dispositivo:" if language == "es" else "Device serial number:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["testDevice"]["serialNumber"], estilo_normal)
                ],
                [
                Paragraph("Modelo de TC:" if language == "es" else "CT model:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["tcModel"], estilo_normal),
                Paragraph("No. de Serie:" if language == "es" else "Serial number:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["deviceSerial"], estilo_normal)
                ],
                [
                Paragraph("Orden de Compra:" if language == "es" else "Purchase order:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["purchaseOrder"], estilo_normal),
                Paragraph("Factor térmico continuo 30°C AMB:" if language == "es" else "Continuous thermal factor 30°C AMB:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["thermalFactor"], estilo_normal)
                ],
                [
                Paragraph("Evaluación general:" if language == "es" else "General evaluation:", estilo_normal),
                Paragraph(evaluation_text_third, estilo_normal),
                Paragraph("Cliente:" if language == "es" else "Customer:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["customer"], estilo_normal)
                ],
                [
                Paragraph("Cantidad:" if language == "es" else "Quantity:", estilo_normal),
                Paragraph(f"{protocolData["thirdCore"]["quantity"]} {"Pieza" if language == "es" else "Piece"}{"s" if int(protocolData["thirdCore"]["quantity"]) > 1 else ""}", estilo_normal),
                Paragraph("Clase de aislamiento:" if language == "es" else "Insulation class:", estilo_normal),
                Paragraph(str(protocolData["thirdCore"]["insulationClass"]), estilo_normal)
                ],
                [
                Paragraph("Tipo de transformador:" if language == "es" else "Transformer type:", estilo_normal),
                Paragraph(third_transformer_text, estilo_normal),
                Paragraph("OF:" if language == "es" else "OF:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["of"], estilo_normal)
                ],
            ]
            tabla_third = Table(
                datos_tabla_third,
                colWidths=[
                ancho_util * 0.20,
                ancho_util * 0.20,
                ancho_util * 0.30,
                ancho_util * 0.30,
                ]
            )
            tabla_third.setStyle(TableStyle([
                ("BOX", (0, 1), (-1, 1), 0.5, colors.black),
                ("BOX", (0, 2), (-1, 2), 0.5, colors.black),
                ("BOX", (0, 3), (-1, 3), 0.5, colors.black),
                ("BOX", (0, 4), (-1, 4), 0.5, colors.black),
                ("BOX", (0, 5), (-1, 5), 0.5, colors.black),
                ("BOX", (0, 6), (-1, 6), 0.5, colors.black),
            ]))
            elementos.append(tabla_third)
            elementos.append(Spacer(1, 12))

            active_data_third = [
                [Paragraph("Activo" if language == "es" else "Active", estilo_bold)],
                [
                Paragraph("Ipn:" if language == "es" else "Ipn:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["ipn"], estilo_normal),
                Paragraph("Pruebas dieléctricas" if language == "es" else "Dielectric tests", estilo_normal)
                ],
                [
                Paragraph("Isn:" if language == "es" else "Isn:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["isn"], estilo_normal),
                Paragraph("Potencial aplicado:" if language == "es" else "Applied potential:", estilo_normal_centered)
                ],
                [
                Paragraph("Carga nominal:" if language == "es" else "Rated burden:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["nominalBurden"], estilo_normal),
                Paragraph(protocolData["thirdCore"]["appliedPotential"], estilo_normal_centered)
                ],
                [
                Paragraph("Norma:" if language == "es" else "Standard:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["rule"], estilo_normal)
                ],
                [
                Paragraph("Aplicación:" if language == "es" else "Application:", estilo_normal),
                Paragraph(f"{application_text} ({protocolData["thirdCore"]["thirdCoreTerminals"]})", estilo_normal),
                Paragraph("Potencial inducido:" if language == "es" else "Induced potential:", estilo_normal_centered)
                ],
                [
                Paragraph("Clase:" if language == "es" else "Class:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["class"], estilo_normal),
                Paragraph(protocolData["thirdCore"]["inducedPotential"], estilo_normal_centered)
                ],
                [
                Paragraph("Frecuencia:" if language == "es" else "Frequency:", estilo_normal),
                Paragraph(str(protocolData["thirdCore"]["frequency"]), estilo_normal),
                Paragraph(f"Identificación de Terminales: {protocolData["thirdCore"]["thirdCoreTerminals"]}" if language == "es" else f"Tap setting: {protocolData["thirdCore"]["thirdCoreTerminals"]}", estilo_normal_centered)
                ],
            ]
            active_table_third = Table(active_data_third, colWidths=[ancho_util * 0.2, ancho_util * 0.4, ancho_util * 0.4])
            active_table_third.setStyle(TableStyle([
                ("BOX", (0, 1), (1, -1), 0.5, colors.black),
                ("BOX", (-1, 2), (-1, -2), 0.5, colors.black),
                ("BOX", (2, 1), (-1, 1), 0.5, colors.black),
                ("BOX", (-1, -1), (-1, -1), 0.5, colors.black),
            ]))
            
            elementos.append(active_table_third)
            elementos.append(Spacer(1, 12))
            
            resistance_data_third = [
                [Paragraph("Resistencia del devanado secundario" if language == "es" else "Secondary widing resistance", estilo_bold)],
                [Paragraph("R-ref (75.0 °C):", estilo_normal), Paragraph(str(protocolData["thirdCore"]["resistance"]["rRef"]), estilo_normal), ""],
                [Paragraph("R-meas (25.0 °C):", estilo_normal), Paragraph(str(protocolData["thirdCore"]["resistance"]["rMeas"]), estilo_normal), ""],        
                ]
            resistance_table_third = Table(resistance_data_third, colWidths=[ancho_util * 0.4, ancho_util * 0.2, ancho_util * 0.4])
            resistance_table_third.setStyle(TableStyle([
                ("BOX", (0, 1), (1, 1), 0.5, colors.black),
                ("BOX", (0, 2), (1, 2), 0.5, colors.black),
            ]))
            
            
            elementos.append(resistance_table_third)
            elementos.append(Spacer(1, 12))

            excitation_data_third = [
                [Paragraph("Excitación" if language == "es" else "Excitation", estilo_bold)],
                [Paragraph(f"V-kn: {protocolData["thirdCore"]["excitation"]["vkn"]}", estilo_normal), Paragraph(f"I-kn: {protocolData["thirdCore"]["excitation"]["ikn"]}", estilo_normal), Paragraph(f"FS: {protocolData["thirdCore"]["excitation"]["fs"]}", estilo_normal), ""],        ]
            excitation_table_third = Table(excitation_data_third, colWidths=[ancho_util * 0.2, ancho_util * 0.2, ancho_util * 0.2, ancho_util * 0.4])
            excitation_table_third.setStyle(TableStyle([
                ("BOX", (0, 1), (0, 1), 0.5, colors.black),
                ("BOX", (1, 1), (1, 1), 0.5, colors.black),
                ("BOX", (2, 1), (2, 1), 0.5, colors.black),
            ]))
            
            elementos.append(excitation_table_third)
            elementos.append(Spacer(1, 12))
            
            # Determinar texto de polaridad según valor y idioma 
            polarity_value_third = protocolData["thirdCore"]["ratio"]["polarity"]
            if language == "es":
                polarity_text_third = "Correcto" if polarity_value_third == 1 else "Incorrecto"
            else:
                polarity_text_third = "Correct" if polarity_value_third == 1 else "Incorrect"

            ratio_data_third = [
                [Paragraph("Relación" if language == "es" else "Ratio", estilo_bold)],
                [
                Paragraph("Relación de transformación:" if language == "es" else "N:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["ratio"]["ncore"], estilo_normal_right),
                Paragraph("Resultados en carga nominal:" if language == "es" else "Results at rated burden:", estilo_normal)
                ],
                [
                Paragraph("£t:" if language == "es" else "£t:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["ratio"]["turnsError"], estilo_normal_right),
                Paragraph("Relación:" if language == "es" else "Ratio:", estilo_normal),
                "",
                "",
                Paragraph(protocolData["thirdCore"]["ratio"]["fileRatio"], estilo_normal)
                ],
                [
                Paragraph("Polaridad:" if language == "es" else "Polarity:", estilo_normal),
                Paragraph(polarity_text_third, estilo_normal_right),
                Paragraph("£:" if language == "es" else "£:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["ratio"]["deviation"], estilo_normal_right),
                Paragraph("fase:" if language == "es" else "phase:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["ratio"]["phase"], estilo_normal)
                ],
                [
                "",
                "",
                Paragraph("£C:" if language == "es" else "£C:", estilo_normal),
                Paragraph(protocolData["thirdCore"]["ratio"]["compositeError"], estilo_normal_right),
                "",
                ""
                ],
            ]
            ratio_table_third = Table(ratio_data_third, colWidths=[ancho_util * 0.25, ancho_util * 0.1, ancho_util * 0.25, ancho_util * 0.1, ancho_util * 0.1, ancho_util * 0.2])
            ratio_table_third.setStyle(TableStyle([
                ("BOX", (0, 1), (1, -1), 0.5, colors.black),
                ("BOX", (2, 1), (-1, 1), 0.5, colors.black),
                ("BOX", (2, 2), (-1, -1), 0.5, colors.black),
            ]))
            
            elementos.append(ratio_table_third)
            elementos.append(PageBreak())
            
            first_chart_object_third = protocolData["thirdCore"]["relationChartData"]
            first_chart_data_third = []
                    
            first_chart_data_headers_third = []
            first_chart_data_relations_third = []
            first_chart_data_points_third = []
            
            for pointsLenght in first_chart_object_third[0]["points"]:
                first_chart_data_points_third.append([])
            
            for obj in first_chart_object_third:
                first_chart_data_headers_third.extend([obj["line"], ""])
                first_chart_data_relations_third.extend([obj["relation"], ""])
                
                for pointValues in obj["points"]:
                    point_index = obj["points"].index(pointValues)
                    
                    for point in pointValues:
                        first_chart_data_points_third[point_index].append(f"{point:.2f}")
                        
                        
            first_chart_data_third_half_len = len(first_chart_data_headers_third) // 2
            new_row = []
            for header in first_chart_data_headers_third[:first_chart_data_third_half_len]:
                new_row.append("ICTef [A]")
                new_row.append("UCTef [V]")
            
            first_chart_data_third.append(first_chart_data_headers_third)
            first_chart_data_third.append(first_chart_data_relations_third)
            first_chart_data_third.append(new_row)
            
            for dataPoint in first_chart_data_points_third:
                first_chart_data_third.append(dataPoint)

            if len(first_chart_object_third) != 1:
                num_cols = len(first_chart_object_third) * 2        
            else:
                num_cols = 12
            
            col_width = ancho_util / num_cols
            first_chart_table_third = LongTable(first_chart_data_third, colWidths=[col_width] * num_cols)
            
            style = TableStyle([
                ('GRID', (0,0), (-1,-1), 1, colors.black),
                ('FONTSIZE', (0,0), (-1,-1), 6),  # Aplica tamaño de fuente 8 a toda la tabla
                ('FONTSIZE', (0,2), (-1,2), 5),  # Aplica tamaño de fuente 8 a toda la tabla
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ])

            # Aplica SPAN para cada par
            for i in range(0, len(first_chart_data_headers_third), 2):
                style.add('SPAN', (i, 0), (i+1, 0))  # fila encabezado
                style.add('SPAN', (i, 1), (i+1, 1))  # fila relación, si quieres

            first_chart_table_third.setStyle(style)

            #elementos.append(first_chart_table_first)
            #elementos.append(Spacer(1, 12))
            
            #######GRAPHIC
            
            # 1️⃣ Crear gráfica log-log
            plt.figure(figsize=(7, 5))

            # Paleta de colores
            colorsPyplot = ['red', 'blue', 'green', 'orange', 'purple', 'brown', 'black', 'pink', 'gray', 'cyan']

            for idx, serie in enumerate(first_chart_object_third):
                # Extraer puntos
                points = np.array(serie["points"])
                x = points[:, 0]
                y = points[:, 1]

                # Etiqueta usando 'line' y 'relation'
                label = f"{serie['line']} ({serie['relation']})"

                plt.loglog(x, y, label=label, color=colorsPyplot[idx % len(colorsPyplot)])

                from matplotlib.ticker import ScalarFormatter
                
                ax = plt.gca()
                ax.xaxis.set_major_formatter(ScalarFormatter())
                ax.yaxis.set_major_formatter(ScalarFormatter())

            # Configurar gráfica
            plt.title("Curva de excitación" if language == "es" else "Excitation curve", fontsize=10, weight='bold')
            plt.xlabel("Corriente de excitación secundaria Ie [A]" if language == "es" else "Secondary excitation current Ie [A]")
            plt.ylabel("Voltaje de excitación [V]" if language == "es" else "Excitation voltage [V]")
            plt.grid(True, which="major", linestyle=":", linewidth=0.3, color="gray")
            plt.legend(fontsize=6)
            plt.tight_layout()

            # 2️⃣ Guardar como SVG
            plt.tight_layout()
            first_chart_uuid_third = f"{uuid.uuid4()}.svg"
            plt.savefig(first_chart_uuid_third, format="svg")
            plt.close()

            # 3️⃣ Cargar SVG con svglib
            drawing = svg2rlg(first_chart_uuid_third)

            # (Opcional) Ajustar tamaño en PDF:
            scale_factor = 0.7
            drawing.scale(scale_factor, scale_factor)
            drawing.width *= scale_factor
            drawing.height *= scale_factor
            
            # Tabla de la primera gráfica 
            
            first_chart_showing_third = []
            
            if len(first_chart_object_third) == 1:
                # Mostrar uno al lado del otro (1 fila, 2 columnas)
                first_chart_showing_third = [
                    [first_chart_table_third, drawing]
                ]
                col_widths = [100, 420]  # Ajusta según el espacio A4 (aprox 520 pt usable)
            else:
                # Mostrar uno arriba del otro (2 filas, 1 columna)
                first_chart_showing_third = [
                    [first_chart_table_third],
                    [drawing]
                ]
                col_widths = [520]  # ancho total en una sola columna

            # Generar la tabla
            first_chart_table_showing_third = LongTable(first_chart_showing_third, colWidths=col_widths)

            first_chart_table_showing_third.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0,0), (-1,-1), "CENTER"),
                ("ALIGN", (-1,-1), (-1,-1), "LEFT"),
            ]))

            # Insertar en ReportLab
            points_length = protocolData["thirdCore"]["relationChartData"][0]["points"]
            
            if len(first_chart_object_third) == 1:
                elementos.append(first_chart_table_showing_third)
                elementos.append(Spacer(1, 12))

            else:
                elementos.append(first_chart_table_third)
                # if len(points_length) > 14 and len(points_length) < 35:
                #     elementos.append(PageBreak())
                
                first_drawing_table = Table([[drawing]], colWidths=[ancho_util])
                first_drawing_table.setStyle([
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ])
                
                elementos.append(Spacer(1, 12))
                elementos.append(first_drawing_table)
                elementos.append(Spacer(1, 12))
            
            if os.path.exists(first_chart_uuid_third):
                os.remove(first_chart_uuid_third)
            
            # Segunda tabla
            
            second_chart_legend_third = protocolData["thirdCore"]["ratioErrorLegend"]
            second_chart_series_third = protocolData["thirdCore"]["ratioErrorSeries"]
                    
            second_chart_table_data_third = [
                [Paragraph("Error de relación de corriente % a % de corriente nominal en carga nominal" if language == "es" else "Current ratio error in % at % of rated current", estilo_bold)],
                [Paragraph(f"{protocolData["thirdCore"]["thirdCoreTerminals"]} ({protocolData["thirdCore"]["ratio"]["fileRatio"]})", estilo_normal_centered)]
            ]
            
            second_chart_table_third_row_third = [
                Paragraph("VA/ Cos phi", estilo_normal_centered),
            ]
            
            for percent in second_chart_series_third[0]["data"]:
                second_chart_table_third_row_third.append(Paragraph(f"{percent[0]}%", estilo_normal_centered))
            
            second_chart_table_third_row_third.append(Paragraph("Designación" if language == "es" else "Designation", estilo_normal_centered))
            second_chart_table_data_third.append(second_chart_table_third_row_third)
            
            for serie in second_chart_series_third:
                serie_index = second_chart_series_third.index(serie)
                row_to_append = [Paragraph(second_chart_legend_third[serie_index]["power"], estilo_normal_centered)]
                
                for serie_data in serie["data"]:
                    row_to_append.append(Paragraph(str(serie_data[1]), estilo_normal_centered))
                
                row_to_append.append(Paragraph(second_chart_legend_third[serie_index]["designation"], estilo_normal_centered))
                second_chart_table_data_third.append(row_to_append)

            second_chart_table_widths_third = [ancho_util * 0.15]
            
            for width in second_chart_series_third[0]["data"]:
                second_chart_table_widths_third.append(ancho_util * 0.65 / len(second_chart_series_third[0]["data"]))
            second_chart_table_widths_third.append(ancho_util * 0.2)
            
            percentCellWidth = 0.6 / len(second_chart_series_third[0]["data"])
            second_chart_table_showing_third = Table(second_chart_table_data_third, colWidths=second_chart_table_widths_third)

            second_chart_table_showing_third.setStyle(TableStyle([
                ("SPAN", (0, 0), (-1, 0)),  # Encabezado
                ("SPAN", (0, 1), (-1, 1)),  # Segunda
                ("GRID", (0, 1), (-1, -1), 0.5, colors.black),
            ]))

            elementos.append(KeepTogether(second_chart_table_showing_third))
            elementos.append(Spacer(1, 12))
            
            # Segunda gráfica
            
            plt.figure(figsize=(7, 4))

            for idx, serie in enumerate(second_chart_series_third):
                name = serie["name"].strip()
                data = serie["data"]
                
                # Filtrar datos válidos (ignorando null)
                x = [point[0] for point in data if point[0] is not None and point[1] is not None]
                y = [point[1] for point in data if point[0] is not None and point[1] is not None]
                
                plt.plot(x, y, marker='o', label=name, color=colorsPyplot[idx % len(colorsPyplot)])

            # Configurar etiquetas y grilla
            plt.xlabel("Porcentaje de corriente secundaria I/Ipn [%]" if language == "es" else "Secondary current percentage I/Ipn [%]")
            plt.ylabel("Error de relación de corriente [%]" if language == "es" else "Current ratio error [%]")
            plt.title("Error de relación de corriente % a % de corriente nominal en carga nominal" if language == "es" else "Current ratio error in % at % of rated current", fontsize=10, weight='bold')

            # Grilla sutil
            plt.grid(True, which="major", linestyle="--", linewidth=0.5, alpha=0.7)

            # Leyenda
            plt.legend(fontsize=6, loc='best')

            # Ajustar límites de X si deseas consistencia visual
            plt.xlim(0, 120)
            # Ajustar límites de Y si deseas consistencia visual
            # plt.ylim(-0.8, 0.05)

            plt.tight_layout()

            # Guardar como SVG
            second_chart_uuid_third = f"{uuid.uuid4()}.svg"
            plt.savefig(second_chart_uuid_third, format="svg")
            plt.close()

            # Insertar en PDF
            drawing = svg2rlg(second_chart_uuid_third)

            # Ajustar para A4
            page_width, page_height = A4
            margen = 36
            usable_width = page_width - 2 * margen
            usable_height = 300

            scale_factor = usable_width / drawing.width
            if drawing.height * scale_factor > usable_height:
                scale_factor = usable_height / drawing.height

            drawing.width *= scale_factor
            drawing.height *= scale_factor
            drawing.scale(scale_factor, scale_factor)

            # Centrar usando Table
            table = Table([[drawing]], colWidths=[usable_width])
            table.setStyle([
                ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ])

            elementos.append(table)
            elementos.append(Spacer(1, 12))

            # Tercera tabla
            
            third_chart_legend_third = protocolData["thirdCore"]["phaseLegend"]
            third_chart_series_third = protocolData["thirdCore"]["phaseSeries"]
            
            third_chart_table_data_third = [
                [Paragraph("Fase en min a % de la corriente nominal en carga nominal" if language == "es" else "Phase in min at % of rated current", estilo_bold)],
                [Paragraph(f"{protocolData["thirdCore"]["thirdCoreTerminals"]} ({protocolData["thirdCore"]["ratio"]["fileRatio"]})", estilo_normal_centered)]
            ]
            
            third_chart_table_third_row_third = [
                Paragraph("VA/ Cos phi", estilo_normal_centered),
            ]
            
            for percent in third_chart_series_third[0]["data"]:
                third_chart_table_third_row_third.append(Paragraph(f"{percent[0]}%", estilo_normal_centered))
            
            third_chart_table_third_row_third.append(Paragraph("Designación" if language == "es" else "Designation", estilo_normal_centered))
            third_chart_table_data_third.append(third_chart_table_third_row_third)

            for serie in third_chart_series_third:
                serie_index = third_chart_series_third.index(serie)
                row_to_append = [Paragraph(third_chart_legend_third[serie_index]["power"], estilo_normal_centered)]
                
                for serie_data in serie["data"]:
                    row_to_append.append(Paragraph(str(serie_data[1]), estilo_normal_centered))
                
                row_to_append.append(Paragraph(third_chart_legend_third[serie_index]["designation"], estilo_normal_centered))
                third_chart_table_data_third.append(row_to_append)

            third_chart_table_widths_third = [ancho_util * 0.15]
            
            for width in third_chart_series_third[0]["data"]:
                third_chart_table_widths_third.append(ancho_util * 0.65 / len(third_chart_series_third[0]["data"]))
            third_chart_table_widths_third.append(ancho_util * 0.2)
            
            percentCellWidth = 0.6 / len(third_chart_series_third[0]["data"])
            third_chart_table_showing_third = Table(third_chart_table_data_third, colWidths=third_chart_table_widths_third)

            third_chart_table_showing_third.setStyle(TableStyle([
                ("SPAN", (0, 0), (-1, 0)),  # Encabezado
                ("SPAN", (0, 1), (-1, 1)),  # Segunda
                ("GRID", (0, 1), (-1, -1), 0.5, colors.black),
            ]))

            elementos.append(KeepTogether(third_chart_table_showing_third))
            elementos.append(Spacer(1, 12))
            
            if os.path.exists(second_chart_uuid_third):
                os.remove(second_chart_uuid_third)
                
            # Segunda gráfica
            
            plt.figure(figsize=(7, 4))

            for idx, serie in enumerate(third_chart_series_third):
                name = serie["name"].strip()
                data = serie["data"]
                
                # Filtrar datos válidos (ignorando null)
                x = [point[0] for point in data if point[0] is not None and point[1] is not None]
                y = [point[1] for point in data if point[0] is not None and point[1] is not None]
                
                plt.plot(x, y, marker='o', label=name, color=colorsPyplot[idx % len(colorsPyplot)])

            # Configurar etiquetas y grilla
            plt.xlabel("Porcentaje de corriente secundaria I/Ipn [%]" if language == "es" else "Secondary current percentage I/Ipn [%]")
            plt.ylabel("Fase [min]" if language == "es" else "Phase [min]")
            plt.title("Fase en min a % de la corriente nominal" if language == "es" else "Phase in min at % of rated current", fontsize=10, weight='bold')

            # Grilla sutil
            plt.grid(True, which="major", linestyle="--", linewidth=0.5, alpha=0.7)

            # Leyenda
            plt.legend(fontsize=6, loc='best')

            # Ajustar límites de X si deseas consistencia visual
            plt.xlim(0, 120)
            # Ajustar límites de Y si deseas consistencia visual
            # plt.ylim(-0.8, 0.05)

            plt.tight_layout()

            # Guardar como SVG
            third_chart_uuid_third = f"{uuid.uuid4()}.svg"
            plt.savefig(third_chart_uuid_third, format="svg")
            plt.close()

            # Insertar ethird
            drawing = svg2rlg(third_chart_uuid_third)

            # Ajustar para A4
            page_width, page_height = A4
            margen = 36
            usable_width = page_width - 2 * margen
            usable_height = 300

            scale_factor = usable_width / drawing.width
            if drawing.height * scale_factor > usable_height:
                scale_factor = usable_height / drawing.height

            drawing.width *= scale_factor
            drawing.height *= scale_factor
            drawing.scale(scale_factor, scale_factor)

            # Centrar usando Table
            table = Table([[drawing]], colWidths=[usable_width])
            table.setStyle([
                ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ])

            elementos.append(table)
            elementos.append(Spacer(1, 12))    
            
            if os.path.exists(third_chart_uuid_third):
                os.remove(third_chart_uuid_third)
    

        elaborated_by_img = Image(protocolData["signatures"]["elaborated"]["url"], width=3*cm, height=3*cm)
        
        signs_images = [
            elaborated_by_img
        ]
        
        if protocolData["signatures"]["revised"]["url"]:
            revised_by_img = Image(protocolData["signatures"]["revised"]["url"], width=3*cm, height=3*cm)
            signs_images.append(revised_by_img)
            
        if protocolData["signatures"]["approved"]["url"]:
            approved_by_img = Image(protocolData["signatures"]["approved"]["url"], width=3*cm, height=3*cm)
            signs_images.append(approved_by_img)
            
        signs_names = [
            Paragraph(f"{"Elaborado" if language == "es" else "Elaborated"}<br/>{protocolData["signatures"]["elaborated"]["name"]}", estilo_normal_centered)
        ]
        
        if protocolData["signatures"]["revised"]["name"]:
            signs_names.append(Paragraph(f"{"Revisado" if language == "es" else "Revised"}<br/>{protocolData["signatures"]["revised"]["name"]}", estilo_normal_centered))
            
        if protocolData["signatures"]["approved"]["name"]:
            signs_names.append(Paragraph(f"{"Aprobado" if language == "es" else "Approved"}<br/>{protocolData["signatures"]["approved"]["name"]}", estilo_normal_centered))
            
        
        signs_data = [
            signs_images,
            signs_names
        ]
        
        signs_table = Table(signs_data, colWidths=[ancho_util*0.33, ancho_util*0.33, ancho_util*0.33], hAlign="CENTER")
        signs_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        
        elementos.append(signs_table)

        # Construir PDF
        doc.build(elementos)
        
        if output_path:
            shutil.move(temp_pdf.name, output_path)
            return  # Nada, porque el background no espera retorno

        else:
            with open(temp_pdf.name, "rb") as f:
                pdf_bytes = f.read()
            return pdf_bytes

    except Exception as e:
        print(f"Error al generar PDF: {e}")
        raise

    finally:
        if os.path.exists(temp_pdf.name):
            os.remove(temp_pdf.name)

class ProtocolDataRequest(BaseModel):
    protocolData: Dict[str, Any]
    language: str = "es"

@app.post("/generate-protocol-reportlab")
def generate_pdf_endpoint(request: ProtocolDataRequest):
    """
    Recibe protocolData y language, genera un PDF y retorna el blob.
    """
    pdf_bytes = generar_pdf(request.protocolData, request.language)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=report.pdf"}
    )

class HtmlZipRequest(BaseModel):
    htmls: List[str]
    offf: str
    language: str = "es"
    filename: str = "documentos.zip"

# Evento global para manejar la cancelación
cancel_task = False

def generar_zip_en_background(job_id: str, request: HtmlZipRequest):
    try:
        job_status[job_id] = "procesando"
        zip_path = f"{zip_folder}/{job_id}.zip"

        """
        Recibe un array de HTMLs, los convierte a PDFs y devuelve un ZIP con todos los PDFs como respuesta.
        """
        # Definir textos según idioma
        if request.language.lower() == "en":
            header_title = "CURRENT TRANSFORMER TEST REPORT"
            company_name = "EQUIPOS ELÉCTRICOS CORE S.A de C.V"
            address1 = "Mirto 36, Lomas de San Miguel, 52928,"
            address2 = "Cd López Mateos, Méx"
            phone = "Tel, +(52) 55 58 87 08 71"
            footer_left = "January 2025 REV-A"
            footer_center = "Page"
            footer_right = "FIT: 132"
        else:
            header_title = "REPORTE DE PRUEBAS A TRANSFORMADOR DE CORRIENTE"
            company_name = "EQUIPOS ELÉCTRICOS CORE S.A de C.V"
            address1 = "Mirto 36, Lomas de San Miguel, 52928,"
            address2 = "Cd López Mateos, Méx"
            phone = "Tel, +(52) 55 58 87 08 71"
            footer_left = "Enero 2025 REV-A"
            footer_center = "Página"
            footer_right = "FIT: 132"
            
        margin_css = f"""
        <style>
        @page {{
            margin: 0cm;
            margin-top: 4.5cm;
            margin-bottom: 2.5cm;
            @top-center {{
                content: element(header);
            }}
            @bottom-left {{
                content: "{footer_left}";
                color: #888;
                font-size: 10px;
                font-family: Arial, sans-serif;
                margin-left: 2cm;
            }}
            @bottom-center {{
                content: "{footer_center} " counter(page) " / " counter(pages);
                color: #888;
                font-size: 10px;
                font-family: Arial, sans-serif;
            }}
            @bottom-right {{
                content: "{footer_right}";
                color: #888;
                font-size: 10px;
                font-family: Arial, sans-serif;
                margin-right: 2cm;
            }}
        }}
        #header {{
            display: grid;
            grid-template-columns: 25% 50% 25%;
            position: running(header);
            width: 18cm;
            height: 3cm;
            background: white;
            font-family: Arial, sans-serif;
        }}
        #footer {{
            display: grid;
            grid-template-columns: 25% 50% 25%;
            width: 20cm;
            text-align: center;
            font-size: 10px;
            color: #888;
            position: running(footer);
            height: 1.5cm;
            background: white;
            font-family: Arial, sans-serif;
        }}
        </style>
        <div id="header">
            <div>
                <img
                    src="https://tendero.blob.core.windows.net/fileseecore/c124a0bb-c546-48f9-a9c9-56443f1554b4.png?sv=2024-08-04&st=2025-06-21T17%3A48%3A11Z&se=2125-06-21T17%3A48%3A11Z&sr=b&sp=r&sig=sQisxXSu5LkJG7RDfSD%2BcV9anlamuqW9X4W4dp8J%2BZ4%3D"
                    style="width: 100px" />
            </div>
            <div style="display: flex; justify-content: center; text-align: center;">
                <h3 style="font-weight: bold; color: black; font-size: 20px; margin: 0;">
                    {header_title}
                </h3>
            </div>
            <div>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {company_name}
                </p>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {address1}
                </p>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {address2}
                </p>
                <p style="color: black; font-size: 10px; text-align: right; font-weight: bold; margin: 0;">
                    {phone}
                </p>
            </div>
        </div>
        <div id="footer">
            <div style="background: white; width: 100%">
                <p>{footer_left}</p>
            </div>
            <div style="background: white; width: 100%">
                <p>{footer_center}</p>
            </div>
            <div style="background: white; width: 100%">
                <p>{footer_right}</p>
            </div>
        </div>
        """

        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zip_file:
            for idx, html in enumerate(request.htmls):
                full_html = margin_css + html
                pdf_io = BytesIO()
                HTML(string=full_html).write_pdf(pdf_io)
                pdf_io.seek(0)
                zip_file.writestr(f"{request.offf}-{idx+1}.pdf", pdf_io.read())

        job_status[job_id] = "completado"
    except Exception as e:
        job_status[job_id] = "error"
        job_errors[job_id] = str(e)

def generate_pdfs_zip_endpoint(job_id: str, request: ProtocolDataListRequest):
    try:
        job_status[job_id] = "procesando"
        zip_path = f"{zip_folder}/{job_id}.zip"

        pdf_files = []

        for idx, protocolData in enumerate(request.protocolDataList):
            temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            temp_pdf.close()
            try:
                # 🩺 generar_pdf debe guardar el PDF en temp_pdf.name sin retornar Response
                generar_pdf(protocolData, request.language, output_path=temp_pdf.name)

                with open(temp_pdf.name, "rb") as f:
                    pdf_bytes = f.read()
                pdf_files.append((f"protocol_{idx+1}.pdf", pdf_bytes))
            finally:
                if os.path.exists(temp_pdf.name):
                    os.remove(temp_pdf.name)

        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zip_file:
            for pdf_name, pdf_bytes in pdf_files:
                zip_file.writestr(pdf_name, pdf_bytes)

        job_status[job_id] = "completado"
    except Exception as e:
        job_status[job_id] = "error"
        job_errors[job_id] = str(e)

##########################################################################

@app.get("/ping")
def ping():
    return {"status": "ok"}