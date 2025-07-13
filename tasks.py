from celery_instance import celery_app
from reportlab.pdfgen import canvas  # si lo necesitas para generar_pdf
from io import BytesIO
import os
from zipfile import ZipFile, ZIP_DEFLATED
import tempfile

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

import logging
logger = logging.getLogger(__name__)


# Asume que ya tienes esta función en otro archivo, si no, impórtala:
from pdf_generator import generar_pdf  # Ajusta con tu estructura real

class ProtocolDataListRequest(BaseModel):
    protocolDataList: List[Dict[str, Any]]
    language: str = "es"
    filename: str = "protocols.zip"
    
# Estos diccionarios deben estar en un lugar centralizado (por ejemplo, celery_app.py o un módulo shared_state.py)
job_status = {}
job_errors = {}
zip_folder = "./zips"

@celery_app.task
def adding(x, y):
    print('addingfunction')
    return x + y

@celery_app.task
def generate_pdfs_zip_task(job_id: str, request_dict: dict):
    print('ejecutando generate')
    from pydantic import parse_obj_as

    try:
        job_status[job_id] = "procesando"
        zip_path = f"{zip_folder}/{job_id}.zip"

        pdf_files = []

        for idx, protocolData in enumerate(request_dict["protocolDataList"]):
            print('generating pdf', idx)
            logger.info("Estoy procesando algo...", idx)
            temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            temp_pdf.close()
            try:
                generar_pdf(protocolData, request_dict["language"], output_path=temp_pdf.name)

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
        print('hubo un error en el generate', e)
        job_status[job_id] = "error"
        job_errors[job_id] = str(e)
