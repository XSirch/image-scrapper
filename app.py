import os
import threading
import io
import requests
import customtkinter as ctk
from PIL import Image
from urllib.parse import urlparse
from tkinter import filedialog, messagebox

# Import the scraping logic from our scrapper module
from scrapper import extract_product_images

import queue

class ImageScrapperApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("Fashion Image Scrapper")
        self.geometry("800x600")
        
        # Grid layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        
        # 1. Top bar for URL input
        self.top_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.top_frame.grid(row=0, column=0, padx=20, pady=20, sticky="ew")
        self.top_frame.grid_columnconfigure(0, weight=1)
        
        self.url_entry = ctk.CTkEntry(self.top_frame, placeholder_text="Cole a URL do Produto Aqui...", height=40)
        self.url_entry.grid(row=0, column=0, padx=(0, 10), sticky="ew")
        
        self.search_button = ctk.CTkButton(self.top_frame, text="Buscar Imagens", height=40, command=self.on_search_clicked)
        self.search_button.grid(row=0, column=1)
        
        # 2. Main content area (Scrollable)
        self.scrollable_frame = ctk.CTkScrollableFrame(self, label_text="Galeria de Imagens")
        self.scrollable_frame.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")
        self.scrollable_frame.grid_columnconfigure((0, 1, 2), weight=1)  # 3 columns for images
        
        # Keep track of current images
        self.image_widgets = []
        
        # Loading label
        self.status_label = ctk.CTkLabel(self, text="", text_color="gray")
        self.status_label.grid(row=2, column=0, pady=(0, 10))
        
        # State variables for heuristic learning
        self.last_url = ""
        self.escalation_level = 1
        
        # Persistent Browser Session via dedicated thread
        self.url_queue = queue.Queue()
        self.worker_thread = threading.Thread(target=self.browser_worker_loop, daemon=True)
        self.worker_thread.start()

    def update_status(self, text):
        self.status_label.configure(text=text)

    def on_search_clicked(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Aviso", "Por favor, insira uma URL válida.")
            return
            
        if not url.startswith("http"):
            url = "https://" + url
            self.url_entry.delete(0, 'end')
            self.url_entry.insert(0, url)

        # Clear previous images
        for widget in self.image_widgets:
            widget.destroy()
        self.image_widgets.clear()
        
        self.search_button.configure(state="disabled")
        
        # State tracking for Escalation
        if url == self.last_url:
            if self.escalation_level >= 4:
                self.update_status("Limite máximo atingido (4/4). Não há mais estratégias para tentar.")
                self.search_button.configure(state="normal")
                return
            self.escalation_level += 1
            self.update_status(f"Nível de Busca {self.escalation_level}/4: Ignorando filtros...")
        else:
            self.last_url = url
            # Load from memory to display correct initial level
            try:
                import json
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
                if domain.startswith("www."):
                    domain = domain[4:]
                with open("site_profiles.json", "r") as f:
                    mem = json.load(f)
                self.escalation_level = mem.get(domain, {}).get("escalation_level", 1)
            except Exception:
                self.escalation_level = 1
                
            if self.escalation_level > 1:
                self.update_status(f"Memória Carregada: Aplicando Busca Nível {self.escalation_level}/4...")
            else:
                self.update_status("Adicionando na fila de busca (Nível 1)...")
        
        # Send task to dedicated worker thread
        self.url_queue.put((url, self.escalation_level))

    def browser_worker_loop(self):
        # Initialize browser ONCE in this dedicated thread
        self.after(0, self.update_status, "Iniciando navegador fantasma (apenas na 1ª vez)...")
        from scrapling.fetchers import StealthySession
        session = StealthySession(headless=True)
        session.start()
        self.after(0, self.update_status, "Navegador ativo! Aguardando buscas.")
        
        import concurrent.futures
        
        while True:
            # Wait for a URL from the GUI
            payload = self.url_queue.get()
            if payload is None:
                break
                
            url, escalation_level = payload
                
            self.after(0, self.update_status, f"Navegador extraindo imagens da página (Força Nvl {escalation_level})...")
            try:
                # Same thread that created the session executes the fetch!
                image_urls = extract_product_images(url, session=session, escalation_level=escalation_level)
                
                if not image_urls:
                    self.after(0, self.on_fetch_completed, False, "Nenhuma imagem encontrada. A heurística pode precisar de ajustes para este site específico.")
                    continue
                    
                self.after(0, self.update_status, f"Encontradas {len(image_urls)} imagens. Baixando miniaturas...")
                
                # Load images concurrently
                def fetch_and_load(item):
                    img_url, row_idx, col_idx = item
                    self.load_thumbnail(img_url, row_idx, col_idx)

                items_to_fetch = []
                for row_idx, chunk in enumerate(self.chunk_list(image_urls, 3)):
                    for col_idx, img_url in enumerate(chunk):
                        items_to_fetch.append((img_url, row_idx, col_idx))
                        
                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                    list(executor.map(fetch_and_load, items_to_fetch))
                        
                self.after(0, self.on_fetch_completed, True, "Busca concluída!")
            except Exception as e:
                self.after(0, self.on_fetch_completed, False, f"Erro: {str(e)}")
            finally:
                self.url_queue.task_done()

    def load_thumbnail(self, img_url, row_idx, col_idx):
        try:
            # Limit download size or just download to display
            # To be safe and fast, we just do a get request and read the image
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            response = requests.get(img_url, headers=headers, timeout=5)
            
            if response.status_code == 200:
                image_data = Image.open(io.BytesIO(response.content))
                
                # Use CTkImage
                ctk_img = ctk.CTkImage(light_image=image_data, size=(200, 200))
                
                # We need to dispatch the UI creation to the main thread
                self.after(0, self.add_image_to_grid, ctk_img, image_data, row_idx, col_idx)
        except Exception as e:
            print(f"Failed to load thumbnail {img_url}: {e}")

    def add_image_to_grid(self, ctk_img, original_image, row_idx, col_idx):
        frame = ctk.CTkFrame(self.scrollable_frame, fg_color="transparent")
        frame.grid(row=row_idx, column=col_idx, padx=10, pady=10)
        
        img_label = ctk.CTkLabel(frame, image=ctk_img, text="")
        img_label.pack(pady=(0, 5))
        
        save_btn = ctk.CTkButton(frame, text="Salvar", width=120, 
                                 command=lambda img=original_image: self.save_image(img))
        save_btn.pack()
        
        self.image_widgets.append(frame)

    def save_image(self, img):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".png", 
            filetypes=[("PNG files", "*.png"), ("JPEG files", "*.jpg"), ("All files", "*.*")],
            title="Salvar Imagem Como"
        )
        if file_path:
            try:
                # Convert RGBA to RGB for JPEG compatibility just in case
                if img.mode in ("RGBA", "P") and file_path.lower().endswith(".jpg"):
                    img = img.convert("RGB")
                    
                img.save(file_path)
                messagebox.showinfo("Sucesso", "Imagem salva com sucesso!")
            except Exception as e:
                messagebox.showerror("Erro", f"Não foi possível salvar a imagem: {e}")

    def on_fetch_completed(self, success, message):
        self.search_button.configure(state="normal")
        self.update_status(message)

    @staticmethod
    def chunk_list(lst, n):
        """Yield successive n-sized chunks from lst."""
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

if __name__ == "__main__":
    ctk.set_appearance_mode("System")  # Modes: system (default), light, dark
    ctk.set_default_color_theme("blue")  # Themes: blue (default), dark-blue, green
    
    app = ImageScrapperApp()
    app.mainloop()
