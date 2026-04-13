import asyncio
import json
from pathlib import Path

import aiohttp
from aiohttp import ClientResponse
from werkzeug.utils import secure_filename

from . import app

# Эндпоинт для загрузки изображений. Его можно найти в документации
# метода [upload()](https://www.dropbox.com/developers/documentation/http/documentation#files-upload).
UPLOAD_LINK = 'https://content.dropboxapi.com/2/files/upload'
# Эндпоинт для создания ссылки на изображение. Его можно найти
# в документации метода [create_shared_link_with_settings()](https://www.dropbox.com/developers/documentation/http/documentation#sharing-create_shared_link_with_settings).
SHARING_LINK = ('https://api.dropboxapi.com/2/'
                'sharing/create_shared_link_with_settings')


class DropboxUploadError(RuntimeError):
    pass


def _get_dropbox_token():
    token = app.config.get('DROPBOX_TOKEN')
    if not token:
        raise DropboxUploadError(
            'Не найден токен Dropbox. Проверьте переменную '
            'DROPBOX_TOKEN в файле .env.'
        )
    return token


def _prepare_images(images):
    files_to_upload = []
    for image in images or []:
        if not image:
            continue
        filename = secure_filename(Path(image.filename).name)
        if not filename:
            continue
        files_to_upload.append((filename, image.read()))
    return files_to_upload


async def _parse_aiohttp_response(response: ClientResponse, action: str):
    body = await response.text()
    message = body.strip() or 'пустой ответ'

    if response.status >= 400:
        raise DropboxUploadError(
            f'Dropbox не смог {action}: {message[:200]}'
        )

    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise DropboxUploadError(
            f'Dropbox вернул некорректный ответ при попытке {action}: '
            f'{message[:200]}'
        ) from error


async def _post_json(session, url, *, headers, action, **kwargs):
    try:
        async with session.post(url, headers=headers, **kwargs) as response:
            return await _parse_aiohttp_response(response, action)
    except aiohttp.ClientError as error:
        raise DropboxUploadError(
            f'Dropbox не смог {action}: ошибка сети.'
        ) from error
    except asyncio.TimeoutError as error:
        raise DropboxUploadError(
            f'Dropbox не ответил вовремя при попытке {action}.'
        ) from error


async def async_upload_files_to_dropbox(images):
    files_to_upload = _prepare_images(images)
    if not files_to_upload:
        return []

    auth_header = f'Bearer {_get_dropbox_token()}'
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [
            upload_file_and_get_url(session, auth_header, filename, content)
            for filename, content in files_to_upload
        ]
        return await asyncio.gather(*tasks)


async def upload_file_and_get_url(session, auth_header, filename, content):
    dropbox_args = json.dumps({
        'autorename': True,
        'mode': 'add',
        'path': f'/{filename}',
    })
    upload_data = await _post_json(
        session,
        UPLOAD_LINK,
        headers={
            'Authorization': auth_header,
            'Content-Type': 'application/octet-stream',
            'Dropbox-API-Arg': dropbox_args
        },
        data=content,
        action=f'загрузить файл "{filename}"'
    )
    path = upload_data.get('path_lower')
    if not path:
        raise DropboxUploadError(
            f'Dropbox не вернул путь к файлу "{filename}".'
        )

    data = await _post_json(
        session,
        SHARING_LINK,
        headers={
            'Authorization': auth_header,
            'Content-Type': 'application/json',
        },
        json={'path': path},
        action=f'создать ссылку для файла "{filename}"'
    )
    if 'url' not in data:
        data = (
            data.get('error', {})
            .get('shared_link_already_exists', {})
            .get('metadata', {})
        )
    url = data.get('url')
    if not url:
        raise DropboxUploadError(
            f'Dropbox не вернул ссылку для файла "{filename}".'
        )
    return url.replace('?dl=0', '?raw=1').replace('&dl=0', '&raw=1')
