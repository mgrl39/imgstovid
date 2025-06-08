import os
from datetime import datetime
from flask import Flask, request, render_template, jsonify, send_file, Response
from werkzeug.utils import secure_filename
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip, ColorClip, TextClip, VideoFileClip
from moviepy.video.fx.all import resize, rotate, fadeout, fadein
from moviepy.config import change_settings
from PIL import Image
import random
import uuid
import json
import subprocess

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
        files = sorted(
            [os.path.join(directory, f) for f in os.listdir(directory)],
            key=os.path.getctime
        )
        for f in files[:-max_files]:  # Mantener solo los últimos max_files
            try:
                os.remove(f)
            except:
                pass

    @staticmethod
    def resize_image(image_path):
        with Image.open(image_path) as img:
            img = img.convert('RGB')
            
            # Calcular el tamaño para llenar completamente la pantalla
            width_ratio = Config.VIDEO_SIZE[0] / img.size[0]
            height_ratio = Config.VIDEO_SIZE[1] / img.size[1]
            scale_ratio = max(width_ratio, height_ratio)
            
            # Escalar la imagen para que cubra toda la pantalla
            new_width = int(img.size[0] * scale_ratio)
            new_height = int(img.size[1] * scale_ratio)
            img = img.resize((new_width, new_height), Image.LANCZOS)
            
            # Recortar desde el centro
            left = (new_width - Config.VIDEO_SIZE[0]) // 2
            top = (new_height - Config.VIDEO_SIZE[1]) // 2
            right = left + Config.VIDEO_SIZE[0]
            bottom = top + Config.VIDEO_SIZE[1]
            
            # Si la imagen es más pequeña que el tamaño objetivo, ajustar
            if new_width < Config.VIDEO_SIZE[0] or new_height < Config.VIDEO_SIZE[1]:
                # Crear un fondo negro del tamaño objetivo
                background = Image.new('RGB', Config.VIDEO_SIZE, (0, 0, 0))
                # Calcular posición para centrar la imagen pequeña
                paste_x = (Config.VIDEO_SIZE[0] - new_width) // 2
                paste_y = (Config.VIDEO_SIZE[1] - new_height) // 2
                background.paste(img, (paste_x, paste_y))
                img = background
            else:
                img = img.crop((left, top, right, bottom))
            
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

    def generate(self):
        try:
            clips = []
            progress_callback = self.options.get('progress_callback', lambda x: None)
            total_steps = len(self.image_files) * 2
            current_step = 0
            
            # Paso 1: Procesar todas las imágenes
            processed_images = []
            for img in self.image_files:
                print(f"Procesando imagen: {img}")
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], 'images', img)
                processed_path = VideoUtils.resize_image(img_path)
                processed_images.append(processed_path)
                current_step += 1
                progress = int((current_step / total_steps) * 100)
                print(f"Progreso: {progress}%")
                progress_callback(progress)
            
            # Paso 2: Generar el video
            for i, processed_path in enumerate(processed_images):
                print(f"Generando clip {i+1} de {len(processed_images)}")
                duration = self.options.get('durations', {}).get(self.image_files[i], Config.DEFAULT_IMAGE_DURATION)
                clip = ImageClip(processed_path)
                clip = clip.set_duration(duration)
                
                if i > 0:
                    transition = self.options.get('transitions', {}).get(str(i), 'fade')
                    if transition in self.transitions:
                        print(f"Aplicando transición: {transition}")
                        clips.extend(self.create_transition(clips[-1], clip, transition))
                    else:
                        print("Aplicando transición por defecto: fade")
                        clips.extend(self.create_transition(clips[-1], clip, 'fade'))
                else:
                    clips.append(clip)
                
                current_step += 1
                progress = int((current_step / total_steps) * 100)
                print(f"Progreso: {progress}%")
                progress_callback(progress)
                
                # Limpiar imagen procesada
                try:
                    os.remove(processed_path)
                except:
                    pass

            print("Concatenando clips...")
            final_clip = concatenate_videoclips(clips, method='compose')
            
            # Añadir audio
            print("Procesando audio...")
            audio_path = os.path.join(app.config['UPLOAD_FOLDER'], 'music', self.audio_file)
            audio = AudioFileClip(audio_path)
            
            audio_start = self.options.get('audio_start', 0)
            if audio_start > 0:
                print(f"Ajustando tiempo de inicio del audio: {audio_start}s")
                audio = audio.subclip(audio_start)
                
            total_duration = final_clip.duration
            audio = audio.set_duration(total_duration)
            audio = audio.volumex(self.options.get('audio_volume', 1.0))
            
            final_clip = final_clip.set_audio(audio)
            
            output_filename = VideoUtils.generate_unique_filename(f"video.{Config.OUTPUT_FORMAT}")
            output_path = os.path.join(app.config['UPLOAD_FOLDER'], 'output', output_filename)
            
            print("Escribiendo video final...")
            final_clip.write_videofile(
                output_path, 
                fps=Config.FPS, 
                codec='libx264', 
                audio_codec='aac',
                verbose=False,
                logger=None
            )
            print("Video generado con éxito!")
            progress_callback(100)
            
            # Limpiar archivos temporales después de generar el video
            self._cleanup_temp_files()
            
            return output_filename
        except Exception as e:
            # Si algo falla, asegurarnos de limpiar los archivos temporales
            self._cleanup_temp_files()
            raise e

    def _cleanup_temp_files(self):
        """Limpia los archivos temporales usados en la generación"""
        # Limpiar imágenes originales
        for img in self.image_files:
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], 'images', img))
            except:
                pass
        
        # Limpiar archivo de audio
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], 'music', self.audio_file))
        except:
            pass

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
    data = request.json
    image_files = data.get('images', [])
    audio_file = data.get('audio')
    options = data.get('options', {})
    
    if not image_files or not audio_file:
        return jsonify({'error': 'Faltan imágenes o audio'}), 400

    try:
        def progress_callback(progress):
            print(f"Progress: {progress}%")
        
        options['progress_callback'] = progress_callback
        generator = VideoGenerator(image_files, audio_file, options)
        output_filename = generator.generate()
        
        # Limpiar videos antiguos
        VideoUtils.clean_old_files(os.path.join(app.config['UPLOAD_FOLDER'], 'output'))
        
        return jsonify({
            'success': True,
            'video_path': output_filename,
            'duration': sum(options.get('durations', {}).get(img, Config.DEFAULT_IMAGE_DURATION) 
                          for img in image_files)
        })
        
    except Exception as e:
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
        # Limpiar directorios de imágenes y música
        for folder in ['images', 'music']:
            folder_path = os.path.join(app.config['UPLOAD_FOLDER'], folder)
            for file in os.listdir(folder_path):
                try:
                    os.remove(os.path.join(folder_path, file))
                except:
                    pass
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
