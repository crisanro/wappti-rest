# services/email_service.py
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.message import EmailMessage
from fpdf import FPDF
from datetime import datetime
from core.config import settings
from firebase_admin import auth

def generate_invoice_pdf(invoice_data: dict) -> bytes:
    """Genera un PDF válido para EEUU basado en el ejemplo de WAPPTI APP"""
    pdf = FPDF()
    pdf.add_page()
    
    # Fuentes y colores
    pdf.set_font("Helvetica", "B", 24)
    
    # Encabezado Empresa
    pdf.cell(0, 10, "WAPPTI APP", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "150 Central Park Square, Suite #2", ln=True)
    pdf.cell(0, 5, "Los Alamos, New Mexico 87544", ln=True)
    pdf.cell(0, 5, "United States", ln=True)
    pdf.cell(0, 5, "+1725-239-2324 | cristhianromero19@outlook.com", ln=True)
    pdf.ln(10)
    
    # Título y Detalles de la Factura
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "INVOICE", ln=True)
    
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(40, 5, "Invoice Number:", border=0)
    pdf.cell(0, 5, f"{invoice_data.get('invoice_number', 'N/A')}", ln=True)
    pdf.cell(40, 5, "Date of Issue:", border=0)
    pdf.cell(0, 5, f"{invoice_data.get('date', datetime.now().strftime('%B %d, %Y'))}", ln=True)
    pdf.ln(10)
    
    # Cliente
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Bill To:", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, invoice_data.get('customer_email', 'Customer'), ln=True)
    pdf.ln(10)
    
    # Tabla de productos
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(90, 8, "Description", border=1)
    pdf.cell(30, 8, "Qty", border=1, align="C")
    pdf.cell(35, 8, "Unit Price", border=1, align="R")
    pdf.cell(35, 8, "Amount", border=1, align="R")
    pdf.ln()
    
    # Fila del producto
    pdf.set_font("Helvetica", "", 10)
    amount = f"${invoice_data.get('amount', '0.00'):.2f}"
    pdf.cell(90, 8, invoice_data.get('description', 'Subscription'), border=1)
    pdf.cell(30, 8, "1", border=1, align="C")
    pdf.cell(35, 8, amount, border=1, align="R")
    pdf.cell(35, 8, amount, border=1, align="R")
    pdf.ln(10)
    
    # Totales
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(155, 8, "Total Amount Due:", align="R")
    pdf.cell(35, 8, f"{amount} USD", align="R")
    
    # Retorna el PDF en formato bytes (para adjuntarlo sin guardarlo en disco)
    return pdf.output(dest='S')

def send_invoice_email(to_email: str, invoice_pdf_bytes: bytes, invoice_number: str):
    """Envía el correo con el PDF adjunto usando SMTP"""
    msg = MIMEMultipart()
    msg['From'] = settings.FROM_EMAIL
    msg['To'] = to_email
    msg['Subject'] = f"Your Wappti App Invoice #{invoice_number}"
    
    # Cuerpo del correo
    body = "Hello,\n\nThank you for your purchase. Attached you will find your invoice.\n\nBest regards,\nThe Wappti App Team"
    msg.attach(MIMEText(body, 'plain'))
    
    # Adjuntar PDF
    part = MIMEApplication(invoice_pdf_bytes, Name=f"Invoice_{invoice_number}.pdf")
    part['Content-Disposition'] = f'attachment; filename="Invoice_{invoice_number}.pdf"'
    msg.attach(part)
    
    # Conexión SMTP y envío
    try:
        server = smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT)
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Error enviando correo SMTP: {e}")
        return False
    
def send_html_email(to_email: str, subject: str, html_body: str):
    """
    Función base para enviar correos HTML usando las credenciales de config.py
    """
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = settings.FROM_EMAIL
    msg['To'] = to_email
    
    # Seteamos el contenido como HTML
    msg.set_content(html_body, subtype='html')

    try:
        # Asumiendo que usas el puerto 587 (STARTTLS). 
        # Si usas 465 (SSL), sería smtplib.SMTP_SSL() en lugar de .SMTP() y sin .starttls()
        with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
            server.starttls() # Asegura la conexión
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
            print(f"✅ Correo enviado exitosamente a {to_email}")
    except Exception as e:
        print(f"❌ Error al enviar correo a {to_email}: {e}")
        # Aquí podrías registrar el error en Sentry o en tu tabla de logs

def process_password_reset_email(email: str):
    try:
        # 1. Firebase nos genera el link seguro con el token
        reset_link = auth.generate_password_reset_link(email)
        
        # 2. Armamos el HTML (Aquí puedes poner tu diseño bonito)
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #333;">
                <h2>Recuperación de Contraseña</h2>
                <p>Hola,</p>
                <p>Recibimos una solicitud para restablecer tu contraseña en WAPPTI.</p>
                <p>Haz clic en el siguiente enlace para crear una nueva contraseña:</p>
                <a href="{reset_link}" style="background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Restablecer Contraseña</a>
                <p><br>Si no solicitaste esto, puedes ignorar este correo.</p>
            </body>
        </html>
        """
        
        # 3. Enviamos el correo
        send_html_email(
            to_email=email, 
            subject="WAPPTI - Recupera tu contraseña", 
            html_body=html_content
        )
    except Exception as e:
        print(f"Error generando link de reseteo para {email}: {e}")

def process_email_verification(email: str):
    try:
        # 1. Firebase genera el link de verificación
        verification_link = auth.generate_email_verification_link(email)
        
        # 2. Armamos el HTML
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #333;">
                <h2>Bienvenido a WAPPTI</h2>
                <p>Hola,</p>
                <p>Para empezar a usar todas las funciones, necesitamos verificar tu correo electrónico.</p>
                <a href="{verification_link}" style="background-color: #28a745; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Verificar mi correo</a>
            </body>
        </html>
        """
        
        # 3. Enviamos el correo
        send_html_email(
            to_email=email, 
            subject="WAPPTI - Verifica tu correo electrónico", 
            html_body=html_content
        )
    except Exception as e:
        print(f"Error generando link de verificación para {email}: {e}")

