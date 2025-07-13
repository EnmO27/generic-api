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

from celery.result import AsyncResult
from celery_instance import celery_app

from weasyprint import HTML
from pdf2image import convert_from_bytes
from io import BytesIO

from zipfile import ZipFile, ZIP_DEFLATED
from pydantic import BaseModel
from typing import List, Dict, Any
from time import sleep

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

from tasks import ProtocolDataListRequest, generate_pdfs_zip_task, generar_pdf, adding, job_status

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
job_ids: Dict[str, str] = {}  # jobId -> "pendiente" | "procesando" | "completado" | "error"
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
    
@app.post("/start-pdfs-generation")
def iniciar_proceso(request: ProtocolDataListRequest):
    job_id = str(uuid.uuid4())
    job_status[job_id] = "pendiente"

    # adding_id = adding.delay(8, 11)
    # adding_result = AsyncResult(adding_id.id, app=celery_app)
    
    # print('the adding result', adding_result.get(timeout=1))

    # Serializar request en dict para pasarlo a Celery
    
    print('local job id', job_id)    
    generate_job_id = generate_pdfs_zip_task.delay(job_id, request.dict())
    
    print('celery id', generate_job_id.id)

    job_ids[generate_job_id.id] = job_id
    return {"jobId": generate_job_id.id}

@app.get("/get-pdfs-status")
async def estado_proceso(jobId: str):
    task_result = AsyncResult(jobId, app=celery_app)
    print('jobstatus', task_result.state)
    if task_result.state == "PENDING":
        estado = "pendiente"
    elif task_result.state == "STARTED":
        estado = "procesando"
    elif task_result.state == "SUCCESS":
        estado = "completado"
    elif task_result.state == "FAILURE":
        estado = "error"
    else:
        estado = "desconocido"

    return {
        "jobId": jobId,
        "estado": estado,
        "error": str(task_result.result) if estado == "error" else None
    }
    
@app.get("/get-pdfs-zip")
async def descargar_zip(jobId: str):
    
    job_local = job_ids[jobId]
    
    # estado = job_status.get(jobId)
    # if estado != "completado":
    #     raise HTTPException(status_code=400, detail=f"ZIP aún no está listo. Estado actual: {estado}")

    zip_path = f"zips/{job_local}.zip"
    if not os.path.exists(zip_path):
        raise HTTPException(status_code=404, detail="ZIP no encontrado")

    def file_iterator():
        with open(zip_path, "rb") as f:
            while chunk := f.read(8192):
                yield chunk

    return StreamingResponse(
        file_iterator(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={job_local}.zip"}
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

class ProtocolDataRequest(BaseModel):
    protocolData: Dict[str, Any]
    language: str = "es"

@app.post("/generate-protocol-reportlab")
def generate_pdf_endpoint(request: ProtocolDataRequest):
    """
    Recibe protocolData y language, genera un PDF y retorna el blob.
    """
    generate_pdf_result = generar_pdf.delay(request.protocolData, request.language)
    pdf_bytes = generate_pdf_result.get()
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