import os
import sys
import requests
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup
import re
import time
from pathlib import Path
import threading
from queue import Queue, Empty
import mimetypes
import webbrowser
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar,
                             QListWidget, QFileDialog, QMessageBox, QGroupBox, QCheckBox,
                             QSpinBox, QComboBox, QSplitter, QTabWidget, QFrame, QScrollArea,
                             QListWidgetItem, QButtonGroup, QRadioButton, QInputDialog)
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt5.QtGui import QFont, QPalette, QColor, QTextCursor

class FileListItem(QListWidgetItem):
    """自定义列表项，存储文件路径信息"""
    def __init__(self, filename, filepath, filesize=0):
        super().__init__()
        self.filepath = filepath
        self.filename = filename
        self.filesize = filesize  # 文件大小（字节）
        
        # 格式化文件大小
        size_str = self.format_file_size(filesize)
        
        # 设置显示文本
        self.setText(f"{filename} - {filepath} ({size_str})")
        # 设置工具提示
        self.setToolTip(f"文件名: {filename}\n路径: {filepath}\n大小: {size_str}\n双击在浏览器中打开")
        
        # 根据文件类型设置不同的颜色
        if filepath.lower().endswith(('.html', '.htm')):
            self.setForeground(QColor(0, 0, 139))  # 深蓝色
        elif filepath.lower().endswith(('.css',)):
            self.setForeground(QColor(0, 100, 0))  # 深绿色
        elif filepath.lower().endswith(('.js',)):
            self.setForeground(QColor(139, 0, 0))  # 深红色
        elif filepath.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.ico', '.svg')):
            self.setForeground(QColor(139, 69, 19))  # 深橙色
        else:
            self.setForeground(QColor(0, 0, 0))  # 黑色

    def format_file_size(self, size_bytes):
        """格式化文件大小"""
        if size_bytes == 0:
            return "0B"
        size_names = ["B", "KB", "MB", "GB"]
        i = 0
        size = size_bytes
        while size >= 1024 and i < len(size_names) - 1:
            size /= 1024.0
            i += 1
        return f"{size:.1f}{size_names[i]}"

class DownloadThread(QThread):
    """下载线程"""
    progress_signal = pyqtSignal(int, int, str)  # 当前进度, 总数, 当前下载项
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)
    file_count_signal = pyqtSignal(int)
    file_added_signal = pyqtSignal(str, str, int)  # 文件名, 文件路径, 文件大小
    
    def __init__(self, saver, url, download_mode, max_depth, delay_ms, thread_count):
        super().__init__()
        self.saver = saver
        self.url = url
        self.download_mode = download_mode
        self.max_depth = max_depth
        self.delay_ms = delay_ms
        self.thread_count = thread_count
        self.is_cancelled = False
        
    def run(self):
        try:
            self.saver.save_website(
                self.url, 
                self.download_mode,
                self.max_depth,
                self.delay_ms,
                self.thread_count,
                self.progress_signal,
                self.log_signal,
                self.file_count_signal,
                self.file_added_signal,
                self.is_cancelled
            )
            if not self.is_cancelled:
                self.finished_signal.emit(True, "下载完成!")
            else:
                self.finished_signal.emit(False, "下载已取消")
        except Exception as e:
            self.finished_signal.emit(False, f"下载失败: {str(e)}")
    
    def cancel(self):
        self.is_cancelled = True
        self.saver.cancel_download()

class WebsiteSaver:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        self.visited_urls = set()
        self.downloaded_resources = set()
        self.initial_domain = ""
        self.save_dir = ""
        self.resource_queue = Queue()
        self.page_queue = Queue()
        self.lock = threading.Lock()
        self.is_cancelled = False
        self.total_files = 0
        self.downloaded_files = 0
        self.delay_ms = 0  # 延时时间（毫秒）
        self.delay_lock = threading.Lock()
        self.last_request_time = 0
        self.thread_count = 2  # 默认线程数
        
    def reset_state(self):
        """重置状态，允许多次下载"""
        self.visited_urls = set()
        self.downloaded_resources = set()
        self.resource_queue = Queue()
        self.page_queue = Queue()
        self.is_cancelled = False
        self.total_files = 0
        self.downloaded_files = 0
        self.last_request_time = 0
        
    def cancel_download(self):
        self.is_cancelled = True
        
    def apply_delay(self):
        """应用延时，确保距离上一次请求至少间隔delay_ms毫秒"""
        if self.delay_ms <= 0:
            return

        with self.delay_lock:
            current_time = time.time() * 1000  # 当前时间（毫秒）
            if self.last_request_time == 0:
                # 第一次请求，不需要等待，直接设置最后请求时间为当前时间
                self.last_request_time = current_time
            else:
                elapsed = current_time - self.last_request_time
                if elapsed < self.delay_ms:
                    # 需要等待的时间
                    wait_time = (self.delay_ms - elapsed) / 1000.0
                    time.sleep(wait_time)
                # 更新最后请求时间为当前时间（如果等待了，就是等待后的时间；如果没有等待，就是当前时间）
                self.last_request_time = time.time() * 1000
        
    def is_valid_url(self, url, current_domain):
        """
        检查URL是否有效
        """
        if not url or url.startswith('javascript:') or url.startswith('mailto:'):
            return False
            
        parsed = urlparse(url)
        return bool(parsed.netloc) and parsed.netloc == self.initial_domain
        
    def get_absolute_url(self, base_url, relative_url):
        """
        将相对URL转换为绝对URL
        """
        return urljoin(base_url, relative_url)
        
    def get_local_path(self, url, save_dir):
        """
        根据URL生成本地保存路径，保持与网站相同的目录结构
        """
        parsed = urlparse(url)
        path = unquote(parsed.path)  # 解码URL中的中文
        
        # 处理根目录情况
        if not path or path == '/':
            path = '/index.html'
        elif path.endswith('/'):
            path += 'index.html'
            
        # 移除开头的斜杠
        if path.startswith('/'):
            path = path[1:]
            
        # 确定文件保存路径
        full_path = os.path.join(save_dir, path)
        
        # 创建目录
        directory = os.path.dirname(full_path)
        os.makedirs(directory, exist_ok=True)
        
        return full_path
        
    def download_file(self, url, filepath, progress_signal=None, file_added_signal=None):
        """
        下载文件并保存到指定路径
        """
        if self.is_cancelled:
            return False
            
        try:
            # 应用延时
            self.apply_delay()
            
            response = self.session.get(url, stream=True, timeout=10)
            response.raise_for_status()
            
            # 获取文件大小
            filesize = int(response.headers.get('content-length', 0))
            
            # 检测编码
            if response.encoding == 'ISO-8859-1':
                encoding = response.apparent_encoding
            else:
                encoding = response.encoding
                
            # 获取文件类型
            content_type = response.headers.get('content-type', '').lower()
            
            # 如果是文本文件，使用正确编码保存
            if 'text/' in content_type:
                content = response.content.decode(encoding, errors='replace')
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                # 更新实际文件大小
                filesize = len(content.encode('utf-8'))
            else:
                # 二进制文件直接保存
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                # 获取实际文件大小
                filesize = os.path.getsize(filepath)
            
            if progress_signal:
                self.downloaded_files += 1
                progress_signal.emit(self.downloaded_files, self.total_files, url)
            
            # 发送文件添加信号（文件名、完整路径和文件大小）
            if file_added_signal and not url.startswith("下载失败"):
                filename = os.path.basename(filepath)
                file_added_signal.emit(filename, filepath, filesize)
            
            return True
        except Exception as e:
            if progress_signal:
                progress_signal.emit(self.downloaded_files, self.total_files, f"下载失败: {url} - {str(e)}")
            return False
            
    def extract_resources(self, soup, html_content, page_url, current_domain, download_mode, 
                         progress_signal=None, file_count_signal=None, file_added_signal=None, depth=0):
        """
        从HTML中提取资源链接并下载
        """
        # 查找所有需要下载的资源
        resource_tags = {
            'link': 'href',
            'script': 'src',
            'img': 'src',
            'source': 'src',
            'audio': 'src',
            'video': 'src',
            'iframe': 'src',
            'embed': 'src',
            'object': 'data'
        }
        
        # 查找CSS中的资源
        css_urls = re.findall(r'url\([\'"]?(.*?)[\'"]?\)', html_content)
        
        for url in css_urls:
            # 移除可能的多余引号
            url = url.strip('\'"')
            absolute_url = self.get_absolute_url(page_url, url)
            if self.is_valid_url(absolute_url, current_domain) and absolute_url not in self.downloaded_resources:
                with self.lock:
                    if absolute_url not in self.downloaded_resources:
                        self.downloaded_resources.add(absolute_url)
                        self.resource_queue.put(absolute_url)
                        self.total_files += 1
                        if file_count_signal:
                            file_count_signal.emit(self.total_files)
        
        # 查找HTML标签中的资源
        for tag, attr in resource_tags.items():
            for element in soup.find_all(tag):
                if element.has_attr(attr):
                    url = element[attr]
                    absolute_url = self.get_absolute_url(page_url, url)
                    
                    if self.is_valid_url(absolute_url, current_domain) and absolute_url not in self.downloaded_resources:
                        with self.lock:
                            if absolute_url not in self.downloaded_resources:
                                self.downloaded_resources.add(absolute_url)
                                self.resource_queue.put(absolute_url)
                                self.total_files += 1
                                if file_count_signal:
                                    file_count_signal.emit(self.total_files)
                                # 更新HTML中的链接为本地相对路径
                                local_path = self.get_local_path(absolute_url, self.save_dir)
                                page_dir = os.path.dirname(self.get_local_path(page_url, self.save_dir))
                                relative_path = os.path.relpath(local_path, page_dir)
                                element[attr] = relative_path
        
        # 如果下载模式不是"仅当前页面"，则处理a标签中的链接
        if download_mode != "current_page" and (download_mode == "all_pages" or depth < self.max_depth):
            for link in soup.find_all('a', href=True):
                href = link['href']
                absolute_url = self.get_absolute_url(page_url, href)
                
                # 检查是否是页面链接（不是资源链接）
                parsed = urlparse(absolute_url)
                path = parsed.path.lower()
                is_resource = any(path.endswith(ext) for ext in ['.css', '.js', '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.ico', '.svg', '.woff', '.ttf', '.eot'])
                
                if (self.is_valid_url(absolute_url, current_domain) and 
                    absolute_url not in self.visited_urls and
                    absolute_url not in self.downloaded_resources and
                    not is_resource):  # 只处理非资源链接
                    
                    with self.lock:
                        if absolute_url not in self.visited_urls and absolute_url not in self.downloaded_resources:
                            self.visited_urls.add(absolute_url)
                            self.downloaded_resources.add(absolute_url)
                            # 存储页面URL和对应的域名
                            parsed = urlparse(absolute_url)
                            page_domain = parsed.netloc
                            self.page_queue.put((absolute_url, page_domain, depth + 1))
                            self.total_files += 1
                            if file_count_signal:
                                file_count_signal.emit(self.total_files)
                            # 更新HTML中的链接为本地相对路径
                            local_path = self.get_local_path(absolute_url, self.save_dir)
                            page_dir = os.path.dirname(self.get_local_path(page_url, self.save_dir))
                            relative_path = os.path.relpath(local_path, page_dir)
                            link['href'] = relative_path
        
        return str(soup)
        
    def resource_downloader(self, progress_signal=None, log_signal=None, file_added_signal=None):
        """资源下载线程函数"""
        while not self.is_cancelled:
            try:
                url = self.resource_queue.get(timeout=1)
                filepath = self.get_local_path(url, self.save_dir)
                # 资源文件下载时也应用延时
                self.download_file(url, filepath, progress_signal, file_added_signal)
                self.resource_queue.task_done()
            except Empty:
                # 检查是否还有页面需要处理
                if self.page_queue.empty() and self.resource_queue.empty():
                    break
                continue
            except Exception as e:
                if log_signal:
                    log_signal.emit(f"资源下载出错: {e}")
                self.resource_queue.task_done()
        
    def page_downloader(self, download_mode, progress_signal, log_signal, 
                       file_count_signal, file_added_signal):
        """页面下载线程函数"""
        while not self.is_cancelled:
            try:
                url, current_domain, depth = self.page_queue.get(timeout=1)
                self.download_page(url, current_domain, download_mode, progress_signal, 
                                 log_signal, file_count_signal, file_added_signal, depth)
                self.page_queue.task_done()
            except Empty:
                # 检查是否还有资源需要处理
                if self.page_queue.empty() and self.resource_queue.empty():
                    break
                continue
            except Exception as e:
                if log_signal:
                    log_signal.emit(f"页面下载出错: {e}")
                self.page_queue.task_done()
    
    def save_website(self, url, download_mode="current_page", max_depth=1, delay_ms=0, thread_count=2,
                    progress_signal=None, log_signal=None, file_count_signal=None, 
                    file_added_signal=None, is_cancelled=False):
        """
        主函数：保存网站
        """
        # 重置状态，允许多次下载
        self.reset_state()
        self.is_cancelled = is_cancelled
        self.max_depth = max_depth
        self.delay_ms = delay_ms  # 设置延时
        self.thread_count = thread_count  # 设置线程数
        
        parsed = urlparse(url)
        self.initial_domain = parsed.netloc
        
        # 创建保存目录
        self.save_dir = os.path.join(os.getcwd(), "saved_websites", self.initial_domain)
        os.makedirs(self.save_dir, exist_ok=True)
        
        if log_signal:
            log_signal.emit(f"开始保存网站: {url}")
            log_signal.emit(f"文件将保存到: {self.save_dir}")
            log_signal.emit(f"所有请求延时: {delay_ms} 毫秒 (包括资源文件)")
            log_signal.emit(f"下载线程数: {thread_count}")
            
            if download_mode == "current_page":
                log_signal.emit(f"下载模式: 仅当前页面")
            elif download_mode == "depth_limited":
                log_signal.emit(f"下载模式: 指定深度下载 (深度: {max_depth})")
            else:
                log_signal.emit(f"下载模式: 全部下载")
        
        # 重置计数器
        self.total_files = 1  # 主页面
        self.downloaded_files = 0
        if file_count_signal:
            file_count_signal.emit(self.total_files)
        
        # 将起始页面添加到队列
        self.page_queue.put((url, self.initial_domain, 0))
        self.visited_urls.add(url)
        self.downloaded_resources.add(url)
        
        # 创建页面下载线程
        page_thread = threading.Thread(
            target=self.page_downloader,
            args=(download_mode, progress_signal, log_signal, 
                 file_count_signal, file_added_signal)
        )
        page_thread.daemon = True
        page_thread.start()
        
        # 创建多个资源下载线程
        resource_threads = []
        for i in range(thread_count):
            thread = threading.Thread(
                target=self.resource_downloader,
                args=(progress_signal, log_signal, file_added_signal)
            )
            thread.daemon = True
            thread.start()
            resource_threads.append(thread)
        
        # 等待所有任务完成
        self.page_queue.join()
        self.resource_queue.join()
        
        # 确保进度条到达100%
        if progress_signal and self.total_files > 0:
            progress_signal.emit(self.total_files, self.total_files, "下载完成")
        
        if not self.is_cancelled and log_signal:
            log_signal.emit("网站保存完成!")
        
    def download_page(self, url, current_domain, download_mode="current_page", 
                     progress_signal=None, log_signal=None, file_count_signal=None, 
                     file_added_signal=None, depth=0):
        """
        下载单个页面
        """
        if self.is_cancelled:
            return
            
        try:
            # 应用延时
            self.apply_delay()
            
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            # 检测编码
            if response.encoding == 'ISO-8859-1':
                encoding = response.apparent_encoding
            else:
                encoding = response.encoding
                
            content = response.content.decode(encoding, errors='replace')
            
            content_type = response.headers.get('content-type', '').lower()
            
            if 'text/html' in content_type:
                # 处理HTML页面
                soup = BeautifulSoup(content, 'html.parser')
                
                # 如果是"仅下载当前页面"模式，不提取资源链接
                if download_mode == "current_page":
                    # 只保存原始HTML内容，不修改链接
                    filepath = self.get_local_path(url, self.save_dir)
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(content)
                else:
                    # 提取并下载资源
                    modified_html = self.extract_resources(
                        soup, content, url, current_domain, download_mode, 
                        progress_signal, file_count_signal, file_added_signal, depth
                    )
                    
                    # 保存修改后的HTML
                    filepath = self.get_local_path(url, self.save_dir)
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(modified_html)
                
                # 获取文件大小
                filesize = os.path.getsize(filepath)
                
                if progress_signal:
                    self.downloaded_files += 1
                    progress_signal.emit(self.downloaded_files, self.total_files, url)
                
                # 发送文件添加信号
                if file_added_signal:
                    filename = os.path.basename(filepath)
                    file_added_signal.emit(filename, filepath, filesize)
                
                if log_signal:
                    log_signal.emit(f"页面保存成功: {url} (深度: {depth})")
                
            else:
                # 处理非HTML资源
                filepath = self.get_local_path(url, self.save_dir)
                if 'text/' in content_type:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(content)
                else:
                    with open(filepath, 'wb') as f:
                        f.write(response.content)
                
                # 获取文件大小
                filesize = os.path.getsize(filepath)
                
                if progress_signal:
                    self.downloaded_files += 1
                    progress_signal.emit(self.downloaded_files, self.total_files, url)
                
                # 发送文件添加信号
                if file_added_signal:
                    filename = os.path.basename(filepath)
                    file_added_signal.emit(filename, filepath, filesize)
                
                if log_signal:
                    log_signal.emit(f"资源保存成功: {url}")
                
        except Exception as e:
            if log_signal:
                log_signal.emit(f"下载页面失败 {url}: {e}")

class WebSnapProUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.saver = WebsiteSaver()
        self.download_thread = None
        self.all_files = []  # 存储所有文件信息
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle('WebSnapPro - 网站代码下载工具')
        self.setGeometry(100, 100, 1200, 900)  # 增大窗口尺寸
        
        # 设置应用样式
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f5f5;
            }
            QGroupBox {
                font-weight: bold;
                border: 2px solid #d0d0d0;
                border-radius: 6px;
                margin-top: 1ex;
                padding-top: 12px;
                background-color: white;
                font-size: 14px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px 0 6px;
                background-color: white;
                color: #2c3e50;
                font-size: 14px;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 10px 18px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:disabled {
                background-color: #bdc3c7;
                color: #7f8c8d;
            }
            QPushButton#cancelButton {
                background-color: #e74c3c;
            }
            QPushButton#cancelButton:hover {
                background-color: #c0392b;
            }
            QLineEdit {
                padding: 10px;
                border: 2px solid #dcdde1;
                border-radius: 5px;
                background-color: white;
                font-size: 14px;
            }
            QTextEdit, QListWidget {
                border: 2px solid #dcdde1;
                border-radius: 5px;
                background-color: white;
                font-size: 14px;
            }
            QProgressBar {
                border: 2px solid #dcdde1;
                border-radius: 5px;
                text-align: center;
                background-color: white;
                height: 25px;
                font-size: 14px;
            }
            QProgressBar::chunk {
                background-color: #3498db;
                border-radius: 3px;
            }
            QLabel {
                background-color: transparent;
                font-size: 14px;
            }
            QRadioButton {
                padding: 6px;
                font-size: 14px;
            }
            QCheckBox {
                font-size: 14px;
            }
            QSpinBox {
                font-size: 14px;
                padding: 5px;
            }
            QComboBox {
                font-size: 14px;
                padding: 5px;
            }
            QListWidget {
                font-family: 'Microsoft YaHei', Arial, sans-serif;
                font-size: 14px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #ecf0f1;
                background-color: white;
                font-size: 14px;
            }
            QListWidget::item:selected {
                background-color: #e3f2fd;
                color: #2c3e50;
                border: 1px solid #bbdefb;
            }
            QListWidget::item:hover {
                background-color: #f8f9fa;
            }
        """)
        
        # 中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # URL输入区域
        url_group = QGroupBox("网站地址")
        url_layout = QHBoxLayout()
        url_layout.setSpacing(12)
        
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("请输入网站URL (例如: https://example.com)")
        url_layout.addWidget(self.url_input)
        
        self.download_btn = QPushButton("开始下载")
        self.download_btn.clicked.connect(self.start_download)
        url_layout.addWidget(self.download_btn)
        
        self.cancel_btn = QPushButton("取消下载")
        self.cancel_btn.setObjectName("cancelButton")
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.cancel_btn.setEnabled(False)
        url_layout.addWidget(self.cancel_btn)
        
        url_group.setLayout(url_layout)
        main_layout.addWidget(url_group)
        
        # 选项区域
        options_group = QGroupBox("下载选项")
        options_layout = QVBoxLayout()
        
        # 下载模式选择
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("下载模式:"))
        
        self.mode_group = QButtonGroup(self)
        self.current_page_radio = QRadioButton("仅当前页面")
        self.current_page_radio.setChecked(True)
        self.mode_group.addButton(self.current_page_radio)
        mode_layout.addWidget(self.current_page_radio)
        
        self.depth_limited_radio = QRadioButton("指定深度下载")
        self.mode_group.addButton(self.depth_limited_radio)
        mode_layout.addWidget(self.depth_limited_radio)
        
        self.all_pages_radio = QRadioButton("全部下载")
        self.mode_group.addButton(self.all_pages_radio)
        mode_layout.addWidget(self.all_pages_radio)
        
        mode_layout.addStretch()
        options_layout.addLayout(mode_layout)
        
        # 其他选项
        other_options_layout = QHBoxLayout()
        
        other_options_layout.addWidget(QLabel("深度:"))
        self.depth_spin = QSpinBox()
        self.depth_spin.setRange(1, 10)
        self.depth_spin.setValue(1)
        self.depth_spin.setToolTip("指定深度下载的爬取深度")
        self.depth_spin.setEnabled(False)
        other_options_layout.addWidget(self.depth_spin)
        
        # 添加延时设置
        other_options_layout.addWidget(QLabel("延时(ms):"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 10000)
        self.delay_spin.setValue(0)
        self.delay_spin.setSingleStep(100)
        self.delay_spin.setToolTip("每个请求之间的延时时间(毫秒)，包括资源文件")
        self.delay_spin.setSuffix(" ms")
        other_options_layout.addWidget(self.delay_spin)
        
        # 添加线程数设置
        other_options_layout.addWidget(QLabel("线程数:"))
        self.thread_spin = QSpinBox()
        self.thread_spin.setRange(1, 20)
        self.thread_spin.setValue(2)
        self.thread_spin.setToolTip("资源下载的线程数，增加线程数可以提高下载速度")
        other_options_layout.addWidget(self.thread_spin)
        
        other_options_layout.addStretch()
        
        self.browse_btn = QPushButton("浏览保存位置")
        self.browse_btn.clicked.connect(self.browse_save_location)
        other_options_layout.addWidget(self.browse_btn)
        
        options_layout.addLayout(other_options_layout)
        options_group.setLayout(options_layout)
        main_layout.addWidget(options_group)
        
        # 连接单选按钮信号
        self.current_page_radio.toggled.connect(self.on_mode_changed)
        self.depth_limited_radio.toggled.connect(self.on_mode_changed)
        self.all_pages_radio.toggled.connect(self.on_mode_changed)
        
        # 进度区域
        progress_group = QGroupBox("下载进度")
        progress_layout = QVBoxLayout()
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%v/%m (%p%)")
        progress_layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("准备就绪")
        progress_layout.addWidget(self.status_label)
        
        progress_group.setLayout(progress_layout)
        main_layout.addWidget(progress_group)
        
        # 文件筛选区域
        filter_group = QGroupBox("文件筛选")
        filter_layout = QHBoxLayout()
        
        filter_layout.addWidget(QLabel("筛选方式:"))
        
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["所有文件", "扩展名", "文件名", "文件大小"])
        self.filter_combo.currentTextChanged.connect(self.on_filter_changed)
        filter_layout.addWidget(self.filter_combo)
        
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("输入筛选条件 (例如: .html, >1MB, example)")
        self.filter_input.setEnabled(False)
        filter_layout.addWidget(self.filter_input)
        
        self.apply_filter_btn = QPushButton("应用筛选")
        self.apply_filter_btn.clicked.connect(self.apply_filter)
        self.apply_filter_btn.setEnabled(False)
        filter_layout.addWidget(self.apply_filter_btn)
        
        self.clear_filter_btn = QPushButton("清除筛选")
        self.clear_filter_btn.clicked.connect(self.clear_filter)
        filter_layout.addWidget(self.clear_filter_btn)
        
        filter_group.setLayout(filter_layout)
        main_layout.addWidget(filter_group)
        
        # 日志和文件列表区域
        log_splitter = QSplitter(Qt.Horizontal)
        
        # 日志区域
        log_group = QGroupBox("下载日志")
        log_layout = QVBoxLayout()
        
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        log_layout.addWidget(self.log_output)
        
        # 添加清空日志按钮
        clear_log_btn = QPushButton("清空日志")
        clear_log_btn.clicked.connect(self.log_output.clear)
        log_layout.addWidget(clear_log_btn)
        
        log_group.setLayout(log_layout)
        log_splitter.addWidget(log_group)
        
        # 文件列表区域
        files_group = QGroupBox("已下载文件")
        files_layout = QVBoxLayout()
        
        self.file_list = QListWidget()
        self.file_list.itemDoubleClicked.connect(self.open_file_in_browser)
        files_layout.addWidget(self.file_list)
        
        # 添加操作按钮
        file_buttons_layout = QHBoxLayout()
        
        open_folder_btn = QPushButton("打开保存文件夹")
        open_folder_btn.clicked.connect(self.open_save_folder)
        file_buttons_layout.addWidget(open_folder_btn)
        
        refresh_btn = QPushButton("刷新列表")
        refresh_btn.clicked.connect(self.refresh_file_list)
        file_buttons_layout.addWidget(refresh_btn)
        
        files_layout.addLayout(file_buttons_layout)
        
        files_group.setLayout(files_layout)
        log_splitter.addWidget(files_group)
        
        log_splitter.setSizes([700, 500])
        main_layout.addWidget(log_splitter, 1)
        
        # 状态栏
        self.statusBar().showMessage("准备就绪")
        
        # 初始化保存目录
        self.saver.save_dir = os.path.join(os.getcwd(), "saved_websites")
        
    def on_mode_changed(self):
        """下载模式改变时的处理"""
        # 只有在"指定深度下载"模式下才启用深度设置
        self.depth_spin.setEnabled(self.depth_limited_radio.isChecked())
        
    def on_filter_changed(self, text):
        """筛选类型改变时的处理"""
        self.filter_input.setEnabled(text != "所有文件")
        self.apply_filter_btn.setEnabled(text != "所有文件")
        if text == "所有文件":
            self.clear_filter()
        
    def apply_filter(self):
        """应用文件筛选"""
        filter_type = self.filter_combo.currentText()
        filter_value = self.filter_input.text().strip()
        
        if not filter_value:
            return
            
        self.file_list.clear()
        
        for filename, filepath, filesize in self.all_files:
            show_item = False
            
            if filter_type == "扩展名":
                if filepath.lower().endswith(tuple(ext.strip() for ext in filter_value.split(','))):
                    show_item = True
                    
            elif filter_type == "文件名":
                if filter_value.lower() in filename.lower():
                    show_item = True
                    
            elif filter_type == "文件大小":
                try:
                    if filter_value.startswith('>'):
                        size_limit = self.parse_size(filter_value[1:])
                        show_item = filesize > size_limit
                    elif filter_value.startswith('<'):
                        size_limit = self.parse_size(filter_value[1:])
                        show_item = filesize < size_limit
                    else:
                        size_limit = self.parse_size(filter_value)
                        show_item = abs(filesize - size_limit) < 1024  # 近似匹配
                except:
                    show_item = False
            
            if show_item:
                item = FileListItem(filename, filepath, filesize)
                self.file_list.addItem(item)
                
    def parse_size(self, size_str):
        """解析大小字符串为字节数"""
        size_str = size_str.strip().upper()
        if size_str.endswith('KB'):
            return int(float(size_str[:-2]) * 1024)
        elif size_str.endswith('MB'):
            return int(float(size_str[:-2]) * 1024 * 1024)
        elif size_str.endswith('GB'):
            return int(float(size_str[:-2]) * 1024 * 1024 * 1024)
        else:
            return int(float(size_str))
                
    def clear_filter(self):
        """清除筛选"""
        self.filter_input.clear()
        self.refresh_file_list()
        
    def refresh_file_list(self):
        """刷新文件列表"""
        self.file_list.clear()
        for filename, filepath, filesize in self.all_files:
            item = FileListItem(filename, filepath, filesize)
            self.file_list.addItem(item)
        
    def start_download(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "警告", "请输入有效的URL")
            return
            
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
            
        self.url_input.setText(url)
        
        # 确定下载模式
        download_mode = "current_page"
        if self.depth_limited_radio.isChecked():
            download_mode = "depth_limited"
        elif self.all_pages_radio.isChecked():
            download_mode = "all_pages"
        
        # 设置最大深度
        max_depth = self.depth_spin.value() if download_mode == "depth_limited" else 999  # 全部下载使用很大的深度值
        
        # 获取延时设置
        delay_ms = self.delay_spin.value()
        
        # 获取线程数设置
        thread_count = self.thread_spin.value()
        
        # 禁用按钮，启用取消按钮
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        
        # 清空日志和文件列表
        self.log_output.clear()
        self.file_list.clear()
        self.all_files.clear()
        self.progress_bar.setValue(0)
        
        # 创建下载线程
        self.download_thread = DownloadThread(
            self.saver, 
            url, 
            download_mode,
            max_depth,
            delay_ms,
            thread_count
        )
        
        # 连接信号
        self.download_thread.progress_signal.connect(self.update_progress)
        self.download_thread.log_signal.connect(self.log_message)
        self.download_thread.finished_signal.connect(self.download_finished)
        self.download_thread.file_count_signal.connect(self.update_total_files)
        self.download_thread.file_added_signal.connect(self.add_file_to_list)
        
        # 启动线程
        self.download_thread.start()
        
    def cancel_download(self):
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.cancel()
            self.log_message("正在取消下载...")
            
    def download_finished(self, success, message):
        self.log_message(message)
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        
        if success:
            self.statusBar().showMessage("下载完成")
            if self.progress_bar.maximum() > 0:
                self.progress_bar.setValue(self.progress_bar.maximum())
        else:
            self.statusBar().showMessage("下载失败")
            
    def update_progress(self, current, total, filename):
        if total > 0:
            if self.progress_bar.maximum() != total:
                self.progress_bar.setMaximum(total)
            
            self.progress_bar.setValue(current)
            
            if not filename.startswith("下载失败"):
                self.status_label.setText(f"进度: {current}/{total} - {os.path.basename(filename)}")
            else:
                self.status_label.setText(f"进度: {current}/{total} - 错误")
                
    def add_file_to_list(self, filename, filepath, filesize):
        """添加文件到文件列表"""
        if filename and not filename.startswith("下载失败") and not filename == "下载完成":
            # 保存文件信息
            self.all_files.append((filename, filepath, filesize))
            
            # 添加到列表
            item = FileListItem(filename, filepath, filesize)
            self.file_list.addItem(item)
            self.file_list.scrollToBottom()
                
    def update_total_files(self, total):
        """更新总文件数"""
        self.progress_bar.setMaximum(total)
                
    def log_message(self, message):
        self.log_output.append(f"{time.strftime('%H:%M:%S')} - {message}")
        self.log_output.moveCursor(QTextCursor.End)
        
    def browse_save_location(self):
        directory = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if directory:
            self.saver.save_dir = directory
            self.log_message(f"保存目录设置为: {directory}")
            
    def open_save_folder(self):
        """打开保存文件夹"""
        if os.path.exists(self.saver.save_dir):
            os.startfile(self.saver.save_dir)
        else:
            QMessageBox.information(self, "提示", "保存目录不存在")
            
    def open_file_in_browser(self, item):
        """在浏览器中打开选中的文件"""
        if isinstance(item, FileListItem):
            filepath = item.filepath
            if os.path.exists(filepath):
                # 检查文件类型，只打开HTML文件
                if filepath.lower().endswith(('.html', '.htm')):
                    try:
                        # 使用默认浏览器打开文件
                        webbrowser.open(f'file:///{filepath}')
                        self.log_message(f"在浏览器中打开: {filepath}")
                    except Exception as e:
                        QMessageBox.warning(self, "警告", f"无法打开文件: {e}")
                else:
                    QMessageBox.information(self, "提示", "只有HTML文件可以在浏览器中打开")
            else:
                QMessageBox.warning(self, "警告", "文件不存在")

def main():
    app = QApplication(sys.argv)
    
    # 设置应用字体
    font = QFont("Microsoft YaHei", 12)
    app.setFont(font)
    
    window = WebSnapProUI()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()