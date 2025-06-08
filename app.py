import os
from datetime import datetime
from flask import Flask, request, render_template, jsonify, send_file, Response, stream_with_context
from werkzeug.utils import secure_filename
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip, ColorClip, TextClip, VideoFileClip
from moviepy.video.fx.all import resize, rotate, fadeout, fadein
from moviepy.config import change_settings
from PIL import Image
import random
import uuid
import json
import subprocess
import queue
import threading
import shutil

# Verificar y configurar ImageMagick
try:
    # Intentar ejecutar convert para verificar la instalación
    subprocess.run(['convert', '-version'], check=True, capture_output=True)
    # Configurar MoviePy para usar ImageMagick
    change_settings({"IMAGEMAGICK_BINARY": "convert"})
except Exception as e:
    print(f"Error al configurar ImageMagick: {str(e)}")

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32MB max-limit

# Cola global para mensajes de progreso
progress_queue = queue.Queue()

def send_progress(progress, message):
    """Envía una actualización de progreso a la cola"""
    progress_queue.put({
        'progress': progress,
        'message': message
    })

# Configuración
class Config:
    ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'm4a'}
    DEFAULT_IMAGE_DURATION = 3
    OUTPUT_FORMAT = 'mp4'
    FPS = 30
    TRANSITION_DURATION = 0.5
    MAX_VIDEO_DURATION = 300  # 5 minutos máximo
    VIDEO_SIZE = (1080, 1920)  # Tamaño vertical para TikTok/Reels
    DEFAULT_FONT_SIZE = 70
    DEFAULT_FONT_COLOR = 'white'
    TEXT_PADDING = 20
    # Lista de fuentes alternativas
    FONT_PATHS = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        'Arial'
    ]

    @classmethod
    def get_available_font(cls):
        """Encuentra la primera fuente disponible de la lista"""
        for font_path in cls.FONT_PATHS:
            if font_path == 'Arial':
                return font_path
            if os.path.exists(font_path):
                return font_path
        return 'Arial'  # Fallback a Arial si no se encuentra ninguna

# Utilidades
class VideoUtils:
    @staticmethod
    def create_directories():
        for folder in ['uploads', 'uploads/images', 'uploads/music', 'uploads/output']:
            os.makedirs(folder, exist_ok=True)

    @staticmethod
    def allowed_file(filename, allowed_extensions):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

    @staticmethod
    def generate_unique_filename(original_filename):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        ext = original_filename.rsplit('.', 1)[1].lower()
        return f"{timestamp}_{unique_id}.{ext}"

    @staticmethod
    def clean_old_files(directory, max_files=10):
        """Mantiene solo los últimos max_files archivos en el directorio"""
        try:
            files = sorted(
                [os.path.join(directory, f) for f in os.listdir(directory)],
                key=os.path.getctime
            )
            for f in files[:-max_files]:  # Mantener solo los últimos max_files
                try:
                    os.remove(f)
                    send_progress(0, f"Limpiando archivo antiguo: {os.path.basename(f)}")
                except Exception as e:
                    send_progress(0, f"Error al limpiar archivo: {str(e)}")
        except Exception as e:
            send_progress(0, f"Error al listar archivos: {str(e)}")

    @staticmethod
    def clean_directory(directory):
        """Limpia todos los archivos en un directorio"""
        try:
            for filename in os.listdir(directory):
                file_path = os.path.join(directory, filename)
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        send_progress(0, f"Eliminado archivo: {filename}")
                except Exception as e:
                    send_progress(0, f"Error al eliminar {filename}: {str(e)}")
        except Exception as e:
            send_progress(0, f"Error al limpiar directorio {directory}: {str(e)}")

    @staticmethod
    def resize_image(image_path):
        with Image.open(image_path) as img:
            img = img.convert('RGB')
            
            # Calcular el ratio de aspecto del video y la imagen
            video_ratio = Config.VIDEO_SIZE[0] / Config.VIDEO_SIZE[1]
            img_ratio = img.size[0] / img.size[1]
            
            # Determinar las dimensiones finales para cubrir completamente
            if img_ratio > video_ratio:
                # Imagen más ancha que el video
                new_height = Config.VIDEO_SIZE[1]
                new_width = int(new_height * img_ratio)
            else:
                # Imagen más alta que el video
                new_width = Config.VIDEO_SIZE[0]
                new_height = int(new_width / img_ratio)
            
            # Redimensionar la imagen manteniendo la proporción
            img = img.resize((new_width, new_height), Image.LANCZOS)
            
            # Calcular las coordenadas para el recorte centrado
            left = (new_width - Config.VIDEO_SIZE[0]) // 2
            top = (new_height - Config.VIDEO_SIZE[1]) // 2
            right = left + Config.VIDEO_SIZE[0]
            bottom = top + Config.VIDEO_SIZE[1]
            
            # Recortar la imagen al tamaño exacto del video
            img = img.crop((left, top, right, bottom))
            
            # Guardar la imagen procesada
            processed_path = image_path.replace('.', '_processed.')
            img.save(processed_path, 'JPEG', quality=95)
            return processed_path

class Transitions:
    @staticmethod
    def fade():
        return lambda t: 1 if t > 0.5 else 2*t

    @staticmethod
    def zoom_in(clip):
        return clip.resize(lambda t: 1 + 0.3*t)

    @staticmethod
    def zoom_out(clip):
        return clip.resize(lambda t: 1.3 - 0.3*t)

    @staticmethod
    def rotate(clip):
        return clip.rotate(lambda t: 360*t)

class VideoGenerator:
    def __init__(self, image_files, audio_file, options=None):
        self.image_files = image_files
        self.audio_file = audio_file
        self.options = options or {}
        self.transitions = {
            'fade': Transitions.fade,
            'zoom_in': Transitions.zoom_in,
            'zoom_out': Transitions.zoom_out,
            'rotate': Transitions.rotate
        }
        self.processed_files = []

    def create_transition(self, clip1, clip2, transition_type):
        duration = Config.TRANSITION_DURATION
        if transition_type == 'fade':
            clip2 = clip2.set_start(clip1.end - duration)
            clip2 = clip2.crossfadein(duration)
            return [clip1, clip2]
        elif transition_type in ['zoom_in', 'zoom_out', 'rotate']:
            clip2 = clip2.set_start(clip1.end)
            clip1 = getattr(Transitions, transition_type)(clip1)
            return [clip1, clip2]
        else:
            # Si la transición no existe, usar fade
            clip2 = clip2.set_start(clip1.end - duration)
            clip2 = clip2.crossfadein(duration)
            return [clip1, clip2]

    def cleanup(self):
        """Limpia todos los archivos temporales usados en la generación"""
        send_progress(95, "Limpiando archivos temporales...")
        
        # Limpiar imágenes procesadas
        for path in self.processed_files:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    send_progress(96, f"Eliminado archivo temporal: {os.path.basename(path)}")
            except Exception as e:
                send_progress(0, f"Error al limpiar archivo temporal: {str(e)}")

        # Limpiar imágenes originales
        for img in self.image_files:
            try:
                path = os.path.join(app.config['UPLOAD_FOLDER'], 'images', img)
                if os.path.exists(path):
                    os.remove(path)
                    send_progress(97, f"Eliminada imagen original: {img}")
            except Exception as e:
                send_progress(0, f"Error al limpiar imagen: {str(e)}")
        
        # Limpiar archivo de audio
        try:
            path = os.path.join(app.config['UPLOAD_FOLDER'], 'music', self.audio_file)
            if os.path.exists(path):
                os.remove(path)
                send_progress(98, f"Eliminado archivo de audio: {self.audio_file}")
        except Exception as e:
            send_progress(0, f"Error al limpiar audio: {str(e)}")

        send_progress(99, "Limpieza completada")

    def generate(self):
        try:
            clips = []
            self.processed_files = []
            
            # Paso 1: Procesar todas las imágenes
            processed_images = []
            for i, img in enumerate(self.image_files, 1):
                send_progress(
                    (i / len(self.image_files)) * 30,  # 0-30%
                    f"Procesando imagen {i}/{len(self.image_files)}: {img}"
                )
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], 'images', img)
                processed_path = VideoUtils.resize_image(img_path)
                processed_images.append(processed_path)
                self.processed_files.append(processed_path)
            
            # Paso 2: Generar clips
            for i, processed_path in enumerate(processed_images, 1):
                send_progress(
                    30 + (i / len(processed_images)) * 30,  # 30-60%
                    f"Generando clip {i}/{len(processed_images)}"
                )
                duration = self.options.get('durations', {}).get(self.image_files[i-1], Config.DEFAULT_IMAGE_DURATION)
                clip = ImageClip(processed_path)
                clip = clip.set_duration(duration)
                
                if i > 1:
                    transition = self.options.get('transitions', {}).get(str(i), 'fade')
                    if transition in self.transitions:
                        send_progress(
                            30 + (i / len(processed_images)) * 30,
                            f"Aplicando transición {transition} al clip {i}"
                        )
                        clips.extend(self.create_transition(clips[-1], clip, transition))
                    else:
                        clips.extend(self.create_transition(clips[-1], clip, 'fade'))
                else:
                    clips.append(clip)
                
                # Limpiar imagen procesada
                try:
                    os.remove(processed_path)
                except:
                    pass

            send_progress(60, "Concatenando clips...")
            final_clip = concatenate_videoclips(clips, method='compose')
            
            # Añadir audio
            send_progress(70, "Procesando audio...")
            audio_path = os.path.join(app.config['UPLOAD_FOLDER'], 'music', self.audio_file)
            audio = AudioFileClip(audio_path)
            
            audio_start = self.options.get('audio_start', 0)
            if audio_start > 0:
                send_progress(75, f"Ajustando tiempo de inicio del audio: {audio_start}s")
                audio = audio.subclip(audio_start)
                
            total_duration = final_clip.duration
            audio = audio.set_duration(total_duration)
            audio = audio.volumex(self.options.get('audio_volume', 1.0))
            
            send_progress(80, "Combinando video y audio...")
            final_clip = final_clip.set_audio(audio)
            
            output_filename = VideoUtils.generate_unique_filename(f"video.{Config.OUTPUT_FORMAT}")
            output_path = os.path.join(app.config['UPLOAD_FOLDER'], 'output', output_filename)
            
            # Escribir el video con actualizaciones de progreso manuales
            send_progress(85, "Iniciando codificación del video...")

            final_clip.write_videofile(
                output_path, 
                fps=Config.FPS, 
                codec='libx264', 
                audio_codec='aac',
                verbose=False,  # Desactivar verbose para evitar conflictos con el progreso
                logger=None  # No usar logger personalizado
            )
            
            # Limpiar archivos temporales
            self.cleanup()
            
            send_progress(100, "¡Video completado!")
            return output_filename
            
        except Exception as e:
            send_progress(0, f"Error en la generación: {str(e)}")
            # Intentar limpiar en caso de error
            try:
                self.cleanup()
            except:
                pass
            raise e

# Crear directorios necesarios al inicio
VideoUtils.create_directories()

@app.route('/')
def index():
    # Obtener lista de videos generados
    videos = sorted(
        [f for f in os.listdir(os.path.join(app.config['UPLOAD_FOLDER'], 'output'))],
        key=lambda x: os.path.getctime(os.path.join(app.config['UPLOAD_FOLDER'], 'output', x)),
        reverse=True
    )[:5]  # Mostrar solo los últimos 5 videos
    return render_template('index.html', videos=videos)

@app.route('/upload_images', methods=['POST'])
def upload_images():
    if 'images[]' not in request.files:
        return jsonify({'error': 'No se encontraron imágenes'}), 400
    
    files = request.files.getlist('images[]')
    filenames = []
    
    for file in files:
        if file and VideoUtils.allowed_file(file.filename, Config.ALLOWED_IMAGE_EXTENSIONS):
            filename = VideoUtils.generate_unique_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'images', filename))
            filenames.append(filename)
    
    # Limpiar archivos antiguos
    VideoUtils.clean_old_files(os.path.join(app.config['UPLOAD_FOLDER'], 'images'))
    return jsonify({'filenames': filenames})

@app.route('/upload_audio', methods=['POST'])
def upload_audio():
    if 'audio' not in request.files:
        return jsonify({'error': 'No se encontró archivo de audio'}), 400
    
    file = request.files['audio']
    if file and VideoUtils.allowed_file(file.filename, Config.ALLOWED_AUDIO_EXTENSIONS):
        filename = VideoUtils.generate_unique_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'music', filename))
        # Limpiar archivos antiguos
        VideoUtils.clean_old_files(os.path.join(app.config['UPLOAD_FOLDER'], 'music'))
        return jsonify({'filename': filename})
    
    return jsonify({'error': 'Formato de archivo no permitido'}), 400

@app.route('/generate_video', methods=['POST'])
def generate_video():
    if 'images[]' not in request.files:
        return jsonify({'error': 'No se encontraron imágenes'}), 400
    
    if 'audio' not in request.files:
        return jsonify({'error': 'No se encontró archivo de audio'}), 400

    try:
        send_progress(0, "Iniciando proceso de generación...")
        
        # Guardar las imágenes
        image_files = []
        for file in request.files.getlist('images[]'):
            if file and VideoUtils.allowed_file(file.filename, Config.ALLOWED_IMAGE_EXTENSIONS):
                filename = VideoUtils.generate_unique_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'images', filename))
                image_files.append(filename)
                send_progress(5, f"Imagen guardada: {filename}")

        # Guardar el audio
        audio_file = request.files['audio']
        if not VideoUtils.allowed_file(audio_file.filename, Config.ALLOWED_AUDIO_EXTENSIONS):
            return jsonify({'error': 'Formato de audio no permitido'}), 400
        
        audio_filename = VideoUtils.generate_unique_filename(audio_file.filename)
        audio_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'music', audio_filename))
        send_progress(10, f"Audio guardado: {audio_filename}")

        # Obtener opciones
        options = json.loads(request.form.get('options', '{}'))
        send_progress(15, "Configuración cargada")
        
        # Generar el video
        generator = VideoGenerator(image_files, audio_filename, options)
        output_filename = generator.generate()
        
        # Limpiar archivos antiguos del output
        VideoUtils.clean_old_files(os.path.join(app.config['UPLOAD_FOLDER'], 'output'))
        
        return jsonify({
            'success': True,
            'video_path': output_filename,
            'duration': sum(options.get('durations', {}).get(img, Config.DEFAULT_IMAGE_DURATION) 
                          for img in image_files)
        })
        
    except Exception as e:
        # En caso de error, intentar limpiar los archivos
        try:
            for img in image_files:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], 'images', img))
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], 'music', audio_filename))
        except:
            pass
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
def download_file(filename):
    try:
        return send_file(
            os.path.join(app.config['UPLOAD_FOLDER'], 'output', filename),
            as_attachment=True,
            download_name=f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        )
    except Exception as e:
        return jsonify({'error': 'Error al descargar el video'}), 404

@app.route('/preview/<filename>')
def preview_file(filename):
    return send_file(
        os.path.join(app.config['UPLOAD_FOLDER'], 'output', filename)
    )

@app.route('/videos')
def list_videos():
    videos = sorted(
        [f for f in os.listdir(os.path.join(app.config['UPLOAD_FOLDER'], 'output'))],
        key=lambda x: os.path.getctime(os.path.join(app.config['UPLOAD_FOLDER'], 'output', x)),
        reverse=True
    )
    return jsonify({'videos': videos})

@app.route('/cleanup', methods=['POST'])
def cleanup():
    """Endpoint para limpiar archivos temporales desde el frontend"""
    try:
        send_progress(0, "Iniciando limpieza de archivos temporales...")
        
        # Limpiar directorios de imágenes y música
        for folder in ['images', 'music']:
            folder_path = os.path.join(app.config['UPLOAD_FOLDER'], folder)
            VideoUtils.clean_directory(folder_path)
            
        send_progress(100, "Limpieza completada")
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/progress')
def progress():
    def generate():
        while True:
            try:
                progress_data = progress_queue.get(timeout=30)  # 30 segundos timeout
                yield f"data: {json.dumps(progress_data)}\n\n"
            except queue.Empty:
                # Si no hay actualizaciones en 30 segundos, cerrar la conexión
                break
    
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream'
    )

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
