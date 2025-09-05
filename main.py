from http.server import BaseHTTPRequestHandler
import json
import urllib.parse
import urllib.request
import time

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        # Servir le fichier HTML
        with open('index.html', 'r', encoding='utf-8') as f:
            self.wfile.write(f.read().encode())
        return

    def do_POST(self):
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            if self.path == '/api/validate':
                result = self.validate_token(data.get('token'))
                self.wfile.write(json.dumps(result).encode())
                
            elif self.path == '/api/clean':
                result = self.clean_messages(data)
                self.wfile.write(json.dumps(result).encode())
                
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode())

    def validate_token(self, token):
        try:
            req = urllib.request.Request('https://discord.com/api/v10/users/@me')
            req.add_header('Authorization', token)
            
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    user_data = json.loads(response.read().decode())
                    return {
                        'success': True,
                        'user': {
                            'id': user_data['id'],
                            'username': user_data['username'],
                            'discriminator': user_data['discriminator'],
                            'avatar': user_data.get('avatar')
                        }
                    }
                else:
                    return {'success': False, 'error': 'Token invalide'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def clean_messages(self, data):
        token = data.get('token')
        channel_id = data.get('channelId')
        user_id = data.get('userId')
        
        if not all([token, channel_id, user_id]):
            return {'success': False, 'error': 'Paramètres manquants'}
        
        deleted = 0
        total = 0
        errors = 0
        last_message_id = None
        
        try:
            while True:
                # Récupérer les messages
                url = f'https://discord.com/api/v10/channels/{channel_id}/messages?limit=50'
                if last_message_id:
                    url += f'&before={last_message_id}'
                
                req = urllib.request.Request(url)
                req.add_header('Authorization', token)
                
                try:
                    with urllib.request.urlopen(req, timeout=10) as response:
                        if response.status == 429:
                            time.sleep(2)
                            continue
                        elif response.status != 200:
                            break
                        
                        messages = json.loads(response.read().decode())
                        if not messages:
                            break
                        
                        # Filtrer les messages de l'utilisateur
                        user_messages = [msg for msg in messages if msg['author']['id'] == user_id]
                        total += len(user_messages)
                        
                        if not user_messages:
                            last_message_id = messages[-1]['id']
                            continue
                        
                        # Supprimer les messages
                        for message in user_messages:
                            try:
                                del_req = urllib.request.Request(
                                    f'https://discord.com/api/v10/channels/{channel_id}/messages/{message["id"]}',
                                    method='DELETE'
                                )
                                del_req.add_header('Authorization', token)
                                
                                with urllib.request.urlopen(del_req, timeout=10) as del_response:
                                    if del_response.status in [200, 204]:
                                        deleted += 1
                                    else:
                                        errors += 1
                                
                                time.sleep(1)  # Rate limit
                                
                            except Exception:
                                errors += 1
                        
                        last_message_id = messages[-1]['id']
                        
                        # Limite pour éviter les timeouts Vercel
                        if deleted + errors > 100:
                            break
                            
                except Exception:
                    errors += 1
                    time.sleep(2)
            
            return {
                'success': True,
                'stats': {
                    'deleted': deleted,
                    'total': total,
                    'errors': errors
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'stats': {
                    'deleted': deleted,
                    'total': total,
                    'errors': errors
                }
            }
