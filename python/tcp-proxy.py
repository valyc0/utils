#!/usr/bin/env python3
"""
TCP Proxy - Inoltra connessioni TCP da una porta locale verso un server remoto
Uso: python tcp-proxy.py <local_port> <remote_host> <remote_port>
Esempio: python tcp-proxy.py 8080 192.168.1.100 80
"""

import socket
import threading
import sys
import time

class TCPProxy:
    def __init__(self, local_port, remote_host, remote_port):
        self.local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.server = None
        
    def handle_client(self, client_socket, addr):
        """Gestisce la connessione di un singolo client"""
        print(f"[*] Connessione ricevuta da {addr[0]}:{addr[1]}")
        
        try:
            # Connessione al server remoto
            remote_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote_socket.connect((self.remote_host, self.remote_port))
            print(f"[*] Connesso al server remoto {self.remote_host}:{self.remote_port}")
            
            # Thread per inoltrare dati client -> server
            def forward_client_to_server():
                try:
                    while True:
                        data = client_socket.recv(4096)
                        if not data:
                            break
                        remote_socket.sendall(data)
                        print(f"[->] Inoltrati {len(data)} bytes al server remoto")
                except Exception as e:
                    print(f"[!] Errore client->server: {e}")
                finally:
                    remote_socket.close()
                    client_socket.close()
            
            # Thread per inoltrare dati server -> client
            def forward_server_to_client():
                try:
                    while True:
                        data = remote_socket.recv(4096)
                        if not data:
                            break
                        client_socket.sendall(data)
                        print(f"[<-] Inoltrati {len(data)} bytes al client")
                except Exception as e:
                    print(f"[!] Errore server->client: {e}")
                finally:
                    remote_socket.close()
                    client_socket.close()
            
            # Avvia i thread di inoltro
            thread_c2s = threading.Thread(target=forward_client_to_server)
            thread_s2c = threading.Thread(target=forward_server_to_client)
            thread_c2s.daemon = True
            thread_s2c.daemon = True
            thread_c2s.start()
            thread_s2c.start()
            
            # Attendi che entrambi i thread terminino
            thread_c2s.join()
            thread_s2c.join()
            
        except Exception as e:
            print(f"[!] Errore nella gestione della connessione: {e}")
        finally:
            client_socket.close()
            print(f"[*] Connessione chiusa con {addr[0]}:{addr[1]}")
    
    def start(self):
        """Avvia il proxy TCP"""
        try:
            # Crea il socket server
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind(('0.0.0.0', self.local_port))
            self.server.listen(5)
            
            print(f"[*] TCP Proxy avviato su porta {self.local_port}")
            print(f"[*] Inoltra verso {self.remote_host}:{self.remote_port}")
            print("[*] In attesa di connessioni... (Ctrl+C per terminare)")
            
            while True:
                client_socket, addr = self.server.accept()
                # Gestisci ogni client in un thread separato
                client_thread = threading.Thread(
                    target=self.handle_client,
                    args=(client_socket, addr)
                )
                client_thread.daemon = True
                client_thread.start()
                
        except KeyboardInterrupt:
            print("\n[*] Interruzione ricevuta, chiusura del proxy...")
        except Exception as e:
            print(f"[!] Errore: {e}")
        finally:
            if self.server:
                self.server.close()
            print("[*] Proxy terminato")

def main():
    if len(sys.argv) != 4:
        print("Uso: python tcp-proxy.py <local_port> <remote_host> <remote_port>")
        print("Esempio: python tcp-proxy.py 8080 192.168.1.100 80")
        sys.exit(1)
    
    try:
        local_port = int(sys.argv[1])
        remote_host = sys.argv[2]
        remote_port = int(sys.argv[3])
        
        if not (1 <= local_port <= 65535) or not (1 <= remote_port <= 65535):
            print("[!] Le porte devono essere comprese tra 1 e 65535")
            sys.exit(1)
        
        proxy = TCPProxy(local_port, remote_host, remote_port)
        proxy.start()
        
    except ValueError:
        print("[!] Errore: le porte devono essere numeri interi")
        sys.exit(1)
    except Exception as e:
        print(f"[!] Errore: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

