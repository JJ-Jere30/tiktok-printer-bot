from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import subprocess
import os
import time
import re
import threading
from datetime import datetime
from io import BytesIO
import requests
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, FollowEvent, ShareEvent, GiftEvent, JoinEvent

app = Flask(__name__)
CORS(app)

# --- CONFIGURACIÓN ---
NOMBRE_IMPRESORA = "XP-58" 
CARPETA_TEMP = "temp_print"
ANCHO_PIXELES = 384  # 48mm exactos

CONFIG = {
    "unique_id": "@omicodm0",
    "estado_impresora": "Desconocido",
    "cola_impresion": [],  # Lista para almacenar los payloads pendientes o fallidos
    "ultima_orden": "Ninguna",
    "estado_tiktok": "Desconectado"
}

LOGS_EVENTOS = []
lock_impresora = threading.Lock()

if not os.path.exists(CARPETA_TEMP):
    os.makedirs(CARPETA_TEMP)

def agregar_log(texto):
    timestamp = datetime.now().strftime("%H:%M:%S")
    LOGS_EVENTOS.insert(0, f"[{timestamp}] {texto}")
    if len(LOGS_EVENTOS) > 60:
        LOGS_EVENTOS.pop()

def limpiar_texto(texto):
    return re.sub(r'[^\x00-\x7f]', r'', texto)

TRADUCCIONES_REGALOS = {
    "HEART": "CORAZÓN", "ROSE": "ROSA", "GG": "GG", 
    "FINGER HEART": "CORAZÓN CON DEDOS", "WAVE": "SALUDO", 
    "GIFT": "REGALO", "TIKTOK": "TIKTOK", "PANDA": "PANDA", "ICE CREAM": "HELADO"
}

def verificar_estado_impresora_fisica():
    """Verifica mediante un comando rápido si Windows detecta la impresora conectada"""
    try:
        resultado = subprocess.run(['wmic', 'printer', 'where', f"Name='{NOMBRE_IMPRESORA}'", 'get', 'WorkOffline'], capture_output=True, text=True)
        if NOMBRE_IMPRESORA.lower() in resultado.stdout.lower() and "FALSE" in resultado.stdout.upper():
            CONFIG["estado_impresora"] = "🟢 En Línea"
            return True
        else:
            CONFIG["estado_impresora"] = "🔴 Desconectada / Apagada"
            return False
    except:
        # Método alternativo si wmic no está disponible
        CONFIG["estado_impresora"] = "🟢 En Línea (Local)"
        return True

def enviar_binario_a_impresora(datos_binarios, descripcion=""):
    with lock_impresora:
        verificar_estado_impresora_fisica()
        try:
            CONFIG["ultima_orden"] = descripcion
            # Guardamos en la cola temporal por si hay que reenviar
            item_cola = {"descripcion": descripcion, "payload": datos_binarios}
            if item_cola not in CONFIG["cola_impresion"]:
                CONFIG["cola_impresion"].insert(0, item_cola)
                if len(CONFIG["cola_impresion"]) > 10:
                    CONFIG["cola_impresion"].pop()

            nombre_archivo = f"print_{int(time.time())}_{os.urandom(2).hex()}.bin"
            file_path = os.path.join(CARPETA_TEMP, nombre_archivo)
            
            with open(file_path, "wb") as f:
                f.write(datos_binarios)
            
            subprocess.run(['RawPrint.exe', NOMBRE_IMPRESORA, file_path], check=True)
            CONFIG["estado_impresora"] = "🟢 En Línea"
            agregar_log(f"🖨️ Impreso con éxito: {descripcion}")
            
            # Si se imprimió bien, lo sacamos de la cola activa de pendientes
            if item_cola in CONFIG["cola_impresion"]:
                CONFIG["cola_impresion"].remove(item_cola)
            return True
        except Exception as e:
            CONFIG["estado_impresora"] = "🔴 Error / Sin Papel / Apagada"
            agregar_log(f"❌ Error de impresión en [{descripcion}]: {e}")
            return False

def imagen_a_payload_escpos(img_pil):
    ancho, alto = img_pil.size
    bytes_por_fila = ancho // 8  
    
    comando_imagen = bytearray([
        0x1D, 0x76, 0x30, 0, 
        bytes_por_fila & 0xFF, (bytes_por_fila >> 8) & 0xFF, 
        alto & 0xFF, (alto >> 8) & 0xFF
    ])
    
    pixels = img_pil.load()
    payload_bits = bytearray()
    
    for y in range(alto):
        for b in range(bytes_por_fila):
            byte_actual = 0
            for bit in range(8):
                x = b * 8 + bit
                if x < ancho:
                    if pixels[x, y] == 0:
                        byte_actual |= (1 << (7 - bit))
            payload_bits.append(byte_actual)
            
    ESC = b'\x1b'
    INIT_PRINTER = ESC + b'@'
    AVANCE_FINAL = b'\r\n\r\n\r\n'
    return INIT_PRINTER + comando_imagen + payload_bits + AVANCE_FINAL

# --- GENERADORES DE TICKETS ---

def imprimir_ticket_seguidor(nickname):
    try:
        nickname_limpio = limpiar_texto(nickname).upper()
        img = Image.new("RGB", (ANCHO_PIXELES, 90), (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.text((10, 10), "****************************************", fill=(0,0,0))
        d.text((10, 35), f"¡NUEVO SEGUIDOR: {nickname_limpio}!", fill=(0,0,0))
        d.text((10, 65), "****************************************", fill=(0,0,0))
        
        payload = imagen_a_payload_escpos(img.convert('1', dither=Image.Dither.FLOYDSTEINBERG))
        enviar_binario_a_impresora(payload, f"Follow - {nickname_limpio}")
    except Exception as e:
        agregar_log(f"Error seguidor: {e}")

def imprimir_ticket_union(nickname):
    try:
        nickname_limpio = limpiar_texto(nickname).upper()
        img = Image.new("RGB", (ANCHO_PIXELES, 60), (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.text((10, 20), f"HELLO {nickname_limpio}", fill=(0,0,0))
        
        payload = imagen_a_payload_escpos(img.convert('1', dither=Image.Dither.FLOYDSTEINBERG))
        enviar_binario_a_impresora(payload, f"Join - {nickname_limpio}")
    except Exception as e:
        agregar_log(f"Error unión: {e}")

def imprimir_regalo_pequeno_lateral(url_foto, nickname, regalo):
    """Regalos pequeños (<5 monedas): Foto miniatura a la izquierda + texto al lado"""
    try:
        nickname_limpio = limpiar_texto(nickname).upper()
        regalo_limpio = TRADUCCIONES_REGALOS.get(limpiar_texto(regalo).upper(), limpiar_texto(regalo).upper())
        
        ticket = Image.new("RGB", (ANCHO_PIXELES, 90), (255, 255, 255))
        
        if url_foto:
            resp = requests.get(url_foto, timeout=5)
            if resp.status_code == 200:
                avatar = Image.open(BytesIO(resp.content)).convert("RGB")
                avatar = avatar.resize((70, 70), Image.Resampling.LANCZOS)
                ticket.paste(avatar, (15, 10))
                
        d = ImageDraw.Draw(ticket)
        d.text((100, 15), nickname_limpio, fill=(0,0,0))
        d.text((100, 45), f"Gracias por 1 x {regalo_limpio}!", fill=(0,0,0))
        d.line([(10, 85), (ANCHO_PIXELES - 10, 85)], fill=0, width=2)
        
        payload = imagen_a_payload_escpos(ticket.convert('1', dither=Image.Dither.FLOYDSTEINBERG))
        enviar_binario_a_impresora(payload, f"Regalo Pequeño - {nickname_limpio} ({regalo_limpio})")
    except Exception as e:
        agregar_log(f"Error regalo lateral: {e}")

def procesar_e_imprimir_regalo_grande(url_foto, nickname, regalo):
    """Regalos grandes (>=5 monedas): Foto centrada en grande"""
    try:
        resp = requests.get(url_foto, timeout=5)
        if resp.status_code != 200: return
        
        img_perfil = Image.open(BytesIO(resp.content)).convert("RGB")
        tamano = min(img_perfil.size)
        img_perfil = img_perfil.crop((0, 0, tamano, tamano))
        
        mascara = Image.new("L", (tamano, tamano), 0)
        ImageDraw.Draw(mascara).ellipse((0, 0, tamano, tamano), fill=255)
        img_circular = Image.new("RGB", (tamano, tamano), (255, 255, 255))
        img_circular.paste(img_perfil, (0, 0), mask=mascara)
        
        alto_foto = int((ANCHO_PIXELES / tamano) * tamano)
        img_foto = img_circular.resize((ANCHO_PIXELES, alto_foto), Image.Resampling.LANCZOS)
        img_foto = ImageEnhance.Brightness(img_foto).enhance(1.35)
        
        regalo_limpio = TRADUCCIONES_REGALOS.get(limpiar_texto(regalo).upper(), limpiar_texto(regalo).upper())
        
        img_txt = Image.new("RGB", (ANCHO_PIXELES, 120), (255, 255, 255))
        d = ImageDraw.Draw(img_txt)
        d.text((20, 10), "****************************************", fill=(0,0,0))
        d.text((20, 35), limpiar_texto(nickname).upper(), fill=(0,0,0))
        d.text((20, 70), f"ENVIO: {regalo_limpio}", fill=(0,0,0))
        d.text((20, 95), "****************************************", fill=(0,0,0))
        
        ticket_completo = Image.new("RGB", (ANCHO_PIXELES, alto_foto + 120), (255, 255, 255))
        ticket_completo.paste(img_foto, (0, 0))
        ticket_completo.paste(img_txt, (0, alto_foto))
        
        payload = imagen_a_payload_escpos(ticket_completo.convert('1', dither=Image.Dither.FLOYDSTEINBERG))
        enviar_binario_a_impresora(payload, f"Regalo Grande - {nickname} ({regalo_limpio})")
    except Exception as e:
        agregar_log(f"Error regalo grande: {e}")

# --- BOT TIKTOK EN SEGUNDO PLANO ---
cliente_activo = None

def iniciar_bot_tiktok(username):
    global cliente_activo
    try:
        CONFIG["estado_tiktok"] = f"Conectando a {username}..."
        agregar_log(f"Iniciando conexión con {username}")
        
        client = TikTokLiveClient(unique_id=username)
        cliente_activo = client

        @client.on(ConnectEvent)
        async def on_connect(event: ConnectEvent):
            CONFIG["estado_tiktok"] = f"🟢 Conectado a {username}"
            agregar_log(f"¡Cazador Activo en @{event.unique_id}!")

        @client.on(JoinEvent)
        async def on_join(event: JoinEvent):
            imprimir_ticket_union(event.user.nickname)

        @client.on(FollowEvent)
        async def on_follow(event: FollowEvent):
            imprimir_ticket_seguidor(event.user.nickname)

        @client.on(GiftEvent)
        async def on_gift(event: GiftEvent):
            nickname = event.user.nickname
            gift_name = event.gift.info.name if hasattr(event.gift, 'info') else "REGALO"
            monedas = event.gift.info.diamond_count if hasattr(event.gift, 'info') else 1
            
            url_avatar = None
            try: url_avatar = event.user.avatar_thumb.url_list[0]
            except: pass
            
            if monedas >= 5:
                procesar_e_imprimir_regalo_grande(url_avatar, nickname, gift_name)
            else:
                imprimir_regalo_pequeno_lateral(url_avatar, nickname, gift_name)

        client.run()
    except Exception as e:
        CONFIG["estado_tiktok"] = "🔴 Error de Conexión"
        agregar_log(f"Error en bot TikTok: {e}")

# --- INTERFAZ WEB FLASK ---

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Panel TikTok Printer - Yere</title>
    <style>
        body { font-family: Arial, sans-serif; background: #121212; color: #e0e0e0; margin: 0; padding: 15px; }
        .container { display: flex; flex-direction: column; gap: 15px; max-width: 1200px; margin: auto; }
        @media(min-width: 768px) { .container { flex-direction: row; } }
        .card { background: #1e1e1e; padding: 20px; border-radius: 10px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); flex: 1; }
        h2 { color: #bb86fc; margin-top: 0; }
        .status-box { padding: 10px; border-radius: 5px; margin-bottom: 10px; font-weight: bold; }
        .online { background: #03dac6; color: #000; }
        .offline { background: #cf6679; color: #000; }
        input, button { width: 100%; padding: 10px; margin-top: 8px; border-radius: 5px; border: none; box-sizing: border-box; }
        input { background: #2d2d2d; color: #fff; }
        button { background: #bb86fc; color: #000; font-weight: bold; cursor: pointer; }
        button:hover { background: #9a67ea; }
        .btn-reenviar { background: #03dac6; color: #000; }
        .logs { background: #000; padding: 10px; border-radius: 5px; height: 350px; overflow-y: auto; font-family: monospace; font-size: 13px; color: #03dac6; }
    </style>
</head>
<body>
    <div class="container">
        <!-- Panel Izquierdo -->
        <div class="card">
            <h2>🖨️ Estado de la Impresora</h2>
            <div id="printer-status" class="status-box online">Verificando...</div>
            <p><b>Órdenes en Cola:</b> <span id="queue-count">0</span></p>
            <p><b>Última Orden:</b> <span id="last-order">-</span></p>
            <button class="btn-reenviar" onclick="forzarReenvio()">🔄 Reenviar Última Orden</button>

            <h2 style="margin-top: 25px;">🎯 Configurar TikTok Live</h2>
            <div id="tiktok-status" class="status-box offline">Estado: Desconectado</div>
            <label>Usuario actual:</label>
            <input type="text" id="username-input" value="{{ unique_id }}">
            <button onclick="cambiarUsuario()">Cambiar y Conectar Cuenta</button>
        </div>

        <!-- Panel Derecho: Consola en vivo -->
        <div class="card" style="flex: 1.5;">
            <h2>📜 Consola de Eventos en Vivo</h2>
            <div class="logs" id="logs-box">Cargando registros...</div>
        </div>
    </div>

    <script>
        function actualizarDatos() {
            fetch('/estado_sistema')
                .then(res => res.json())
                .then(data => {
                    let pBox = document.getElementById('printer-status');
                    pBox.innerText = "Impresora: " + data.estado_impresora;
                    pBox.className = "status-box " + (data.estado_impresora.includes("En Línea") ? "online" : "offline");
                    
                    document.getElementById('queue-count').innerText = data.cola_impresion.length;
                    document.getElementById('last-order').innerText = data.ultima_orden;
                    
                    let tkBox = document.getElementById('tiktok-status');
                    tkBox.innerText = data.estado_tiktok;
                    tkBox.className = "status-box " + (data.estado_tiktok.includes("Conectado") ? "online" : "offline");

                    let logsHtml = "";
                    data.logs.forEach(log => { logsHtml += log + "<br>"; });
                    document.getElementById('logs-box').innerHTML = logsHtml;
                });
        }

        function cambiarUsuario() {
            let nuevoUser = document.getElementById('username-input').value;
            fetch('/conectar_tiktok', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: nuevoUser})
            }).then(res => res.json()).then(data => { alert(data.mensaje); });
        }

        function forzarReenvio() {
            fetch('/forzar_reconexion', {method: 'POST'})
                .then(res => res.json()).then(data => { alert(data.mensaje); });
        }

        setInterval(actualizarDatos, 1500);
        actualizarDatos();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, unique_id=CONFIG["unique_id"])

@app.route('/estado_sistema')
def estado_sistema():
    verificar_estado_impresora_fisica()
    return jsonify({
        "estado_impresora": CONFIG["estado_impresora"],
        "cola_impresion": CONFIG["cola_impresion"],
        "ultima_orden": CONFIG["ultima_orden"],
        "estado_tiktok": CONFIG["estado_tiktok"],
        "logs": LOGS_EVENTOS
    })

@app.route('/conectar_tiktok', methods=['POST'])
def conectar_tiktok():
    data = request.json
    nuevo_user = data.get("username", "@omicodm0")
    if not nuevo_user.startswith("@"):
        nuevo_user = "@" + nuevo_user
    CONFIG["unique_id"] = nuevo_user
    
    hilo = threading.Thread(target=iniciar_bot_tiktok, args=(nuevo_user,), daemon=True)
    hilo.start()
    return jsonify({"mensaje": f"Intentando conectar a {nuevo_user}..."})

@app.route('/forzar_reconexion', methods=['POST'])
def forzar_reconexion():
    if CONFIG["cola_impresion"]:
        item = CONFIG["cola_impresion"][0]
        hilo = threading.Thread(target=enviar_binario_a_impresora, args=(item["payload"], item["descripcion"] + " (Reenviado)"))
        hilo.start()
        return jsonify({"mensaje": "Reenviando última orden de la cola..."})
    else:
        return jsonify({"mensaje": "No hay órdenes pendientes en la cola para reenviar."})

if __name__ == '__main__':
    hilo_inicial = threading.Thread(target=iniciar_bot_tiktok, args=(CONFIG["unique_id"],), daemon=True)
    hilo_inicial.start()
    
    app.run(host='0.0.0.0', port=5000)
                                              
