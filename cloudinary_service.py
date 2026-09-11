"""
cloudinary_service.py - Módulo de integração com Cloudinary
Gerencia upload, otimização automática (WebP / auto-compressão) e exclusão de imagens.
Todas as credenciais são lidas estritamente de variáveis de ambiente (.env).
"""

import os
import re
import uuid
import logging
from typing import Optional
from werkzeug.utils import secure_filename

logger = logging.getLogger("cloudinary_service")

_cloudinary_lib = None
_configured = False


def _init_cloudinary():
    """Inicializa a configuração do SDK do Cloudinary se as credenciais estiverem disponíveis."""
    global _cloudinary_lib, _configured
    if _configured:
        return True

    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key = os.environ.get("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "").strip()
    cloudinary_url = os.environ.get("CLOUDINARY_URL", "").strip()

    if not ((cloud_name and api_key and api_secret) or cloudinary_url):
        return False

    try:
        import cloudinary
        import cloudinary.uploader
        import cloudinary.api

        if cloudinary_url:
            cloudinary.config(cloudinary_url=cloudinary_url, secure=True)
        else:
            cloudinary.config(
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=api_secret,
                secure=True,
            )

        _cloudinary_lib = cloudinary
        _configured = True
        return True
    except ImportError:
        logger.warning("Pacote 'cloudinary' não instalado. Usando armazenamento local.")
        return False
    except Exception as e:
        logger.error(f"Erro ao configurar Cloudinary: {e}")
        return False


def is_cloudinary_configured() -> bool:
    """Verifica se o Cloudinary está devidamente configurado e pronto para uso."""
    return _init_cloudinary()


def extrair_public_id(url: str) -> Optional[str]:
    """
    Extrai o public_id de uma URL do Cloudinary para permitir exclusão.
    Ex: https://res.cloudinary.com/demo/image/upload/v12345/atelie/materiais/foto.webp -> atelie/materiais/foto
    """
    if not url or "cloudinary.com" not in url:
        return None
    try:
        url_clean = url.split("?")[0]
        partes = url_clean.split("/upload/")
        if len(partes) > 1:
            caminho_com_versao = partes[1]
            caminho = re.sub(r"^v\d+/", "", caminho_com_versao)
            public_id = os.path.splitext(caminho)[0]
            return public_id
    except Exception as e:
        logger.warning(f"Não foi possível extrair public_id de {url}: {e}")
    return None


def upload_imagem(
    file_storage,
    folder: str = "materiais",
    fallback_dir: Optional[str] = None,
    custom_id: Optional[str] = None
) -> Optional[str]:
    """
    Faz upload de uma imagem diretamente para o Cloudinary com otimização automática.
    Retorna a URL HTTPS completa da imagem.
    Se o Cloudinary falhar ou não estiver configurado, salva no diretório local de fallback.
    """
    if not file_storage or not getattr(file_storage, "filename", None):
        return None

    # Tenta upload no Cloudinary
    if _init_cloudinary():
        try:
            import cloudinary.uploader

            if hasattr(file_storage, "seek"):
                file_storage.seek(0)

            upload_params = {
                "folder": f"atelie/{folder}",
                "resource_type": "image",
                "transformation": [
                    {"quality": "auto", "fetch_format": "auto"}
                ]
            }
            if custom_id:
                upload_params["public_id"] = custom_id

            res = cloudinary.uploader.upload(file_storage, **upload_params)
            secure_url = res.get("secure_url")
            if secure_url:
                return secure_url
        except Exception as e:
            logger.error(f"Falha no upload para o Cloudinary ({e}). Tentando fallback local.")

    # Fallback local seguro (caso esteja offline ou sem credenciais)
    if fallback_dir:
        try:
            os.makedirs(fallback_dir, exist_ok=True)
            prefix = custom_id or str(uuid.uuid4())
            filename = secure_filename(f"{prefix}_{file_storage.filename}")
            caminho = os.path.join(fallback_dir, filename)
            if hasattr(file_storage, "seek"):
                file_storage.seek(0)
            file_storage.save(caminho)
            return filename
        except Exception as err:
            logger.error(f"Falha também no fallback local: {err}")

    return None


def deletar_imagem(identificador: str, uploads_dir: Optional[str] = None) -> bool:
    """
    Deleta uma imagem. Se for uma URL do Cloudinary, deleta via API.
    Se for um nome de arquivo local, deleta do disco.
    """
    if not identificador:
        return False

    # Caso 1: URL do Cloudinary
    if "cloudinary.com" in identificador:
        public_id = extrair_public_id(identificador)
        if public_id and _init_cloudinary():
            try:
                import cloudinary.uploader
                res = cloudinary.uploader.destroy(public_id)
                return res.get("result") in ("ok", "not found")
            except Exception as e:
                logger.error(f"Erro ao excluir do Cloudinary ({public_id}): {e}")
                return False

    # Caso 2: Arquivo local em uploads_dir
    if uploads_dir:
        caminho = os.path.join(uploads_dir, identificador)
        try:
            if os.path.exists(caminho):
                os.remove(caminho)
                return True
        except Exception as e:
            logger.error(f"Erro ao excluir arquivo local ({caminho}): {e}")

    return False
