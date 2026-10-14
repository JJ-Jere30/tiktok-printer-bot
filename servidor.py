from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image
import subprocess
import os
import time

app = Flask(__name__)
CORS(app)

# --- CONFIGURACIÓN ---
NOMBRE_IMPRESORA = "XP-58" 
CARPETA_TEMP = "temp_print"
ANCHO_PIXELES = 384  # 48mm exactos

if not os.path.exists(CARPETA_TEMP):
    os.makedirs(CARPETA_TEMP)

def limpiar_temporales():
    ahora = time.time()
    for f in os.listdir(CARPETA_TEMP):
        ruta = os.path.join(CARPETA_TEMP, f)
        if os.stat(ruta).st_mtime < ahora - 3600:
            os.remove(ruta)

@app.route('/imprimir_texto', methods=['POST'])
def imprimir_texto():
    limpiar_temporales()
    data = request.json
    texto = data.get('texto', '')
    
    if not texto:
        return jsonify({"error": "No enviaste texto"}), 400

    texto_formateado = texto.replace('\n', '\r\n')
    texto_formateado += "\r\n\r\n\r\n"

    ESC = b'\x1b'
    FS = b'\x1c'
    INIT_PRINTER = ESC + b'@'          
    APAGAR_CHINO = FS + b'.'           
    TABLA_LATINO = ESC + b't\x10'     

    texto_en_bytes = texto_formateado.encode('cp1252', errors='replace')
    datos_finales = INIT_PRINTER + APAGAR_CHINO + TABLA_LATINO + texto_en_bytes

    nombre_archivo = f"nota_{int(time.time())}.txt"
    file_path = os.path.join(CARPETA_TEMP, nombre_archivo)
    
    with open(file_path, "wb") as f:
        f.write(datos_finales)
    
    try:
        subprocess.run(['RawPrint.exe', NOMBRE_IMPRESORA, file_path], check=True)
        return jsonify({"status": "Texto enviado a la Roccia"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/imprimir_imagen', methods=['POST'])
def imprimir_imagen():
    limpiar_temporales()
    if 'foto' not in request.files:
        return jsonify({"error": "No se recibió ninguna imagen"}), 400
    
    archivo = request.files['foto']
    ruta_original = os.path.join(CARPETA_TEMP, "original.jpg")
    archivo.save(ruta_original)

    try:
        img = Image.open(ruta_original)
        
        # 1. Redimensionamos proporcionalmente al ancho de la tiquetera (384 px)
        w_percent = (ANCHO_PIXELES / float(img.size[0]))
        h_size = int((float(img.size[1]) * float(w_percent)))
        img = img.resize((ANCHO_PIXELES, h_size), Image.Resampling.LANCZOS)
        
        # 2. Convertimos a Blanco y Negro puro (1-bit por píxel)
        img = img.convert('1') 
        
        # --- TRUCO MAESTRO: CONVERSIÓN A COMANDOS NATIVOS ESC/POS ---
        width, height = img.size
        bytes_por_fila = width // 8  # 384 / 8 = 48 bytes por fila
        
        # Comando de Hardware: GS v 0 (Imprimir mapa de bits directo)
        # Estructura: GS v 0 m xL xH yL yH
        comando_imagen = bytearray([
            0x1D, 0x76, 0x30, 0, 
            bytes_por_fila & 0xFF, (bytes_por_fila >> 8) & 0xFF, 
            height & 0xFF, (height >> 8) & 0xFF
        ])
        
        pixels = img.load()
        payload_bits = bytearray()
        
        # Recorremos la foto píxel por píxel para armar los bytes correspondientes
        for y in range(height):
            for b in range(bytes_por_fila):
                byte_actual = 0
                for bit in range(8):
                    x = b * 8 + bit
                    if x < width:
                        # En PIL, 0 es negro. En la impresora, 1 es pintar con calor (negro).
                        if pixels[x, y] == 0:
                            byte_actual |= (1 << (7 - bit))
                payload_bits.append(byte_actual)
        
        # Inicialización y espaciado final para el corte
        ESC = b'\x1b'
        INIT_PRINTER = ESC + b'@'
        AVANCE_FINAL = b'\r\n\r\n\r\n'
        
        # Unimos todo el paquete binario listo para el hardware
        datos_impresion = INIT_PRINTER + comando_imagen + payload_bits + AVANCE_FINAL

        # 3. Guardamos como un archivo binario puro (.bin)
        ruta_lista = os.path.join(CARPETA_TEMP, f"print_{int(time.time())}.bin")
        with open(ruta_lista, "wb") as f:
            f.write(datos_impresion)

        # 4. Enviamos el binario traducido a la impresora
        subprocess.run(['RawPrint.exe', NOMBRE_IMPRESORA, ruta_lista], check=True)
        return jsonify({"status": "Imagen procesada e impresa"}), 200
    
    except Exception as e:
        return jsonify({"error": f"Fallo al procesar imagen: {str(e)}"}), 500

@app.route('/estado', methods=['GET'])
def estado():
    return jsonify({
        "mensaje": "Servidor listo",
        "config": {"ancho": "48mm", "impresora": NOMBRE_IMPRESORA}
    }), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)