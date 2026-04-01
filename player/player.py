# 播放器模块
import vlc
import os
import time
import logging
import subprocess
from config import Config

logger = logging.getLogger(__name__)

class Player:
    def __init__(self):
        try:
            # 尝试使用指定的VLC路径
            if os.path.exists(Config.VLC_PATH):
                logger.info(f"使用指定的VLC路径: {Config.VLC_PATH}")
                self.instance = vlc.Instance(f'--no-xlib --verbose=2 --plugin-path={os.path.dirname(Config.VLC_PATH)}\plugins')
            else:
                logger.info("使用系统默认的VLC")
                self.instance = vlc.Instance('--no-xlib --verbose=2')
            
            self.player = self.instance.media_player_new()
            self.current_media = None
            self.screen_index = 0
            self.browser_process = None
            logger.info("播放器初始化成功")
        except Exception as e:
            logger.error(f"播放器初始化失败: {str(e)}")
            self.instance = None
            self.player = None
    
    def play_local(self, file_path):
        """播放本地视频"""
        try:
            if not self.player:
                logger.error("播放器未初始化")
                return False
                
            if not os.path.exists(file_path):
                logger.error(f"文件不存在: {file_path}")
                return False
            
            # 停止当前播放
            self.stop()
            
            logger.info(f"准备播放本地视频: {file_path}")
            media = self.instance.media_new(file_path)
            logger.info(f"媒体对象创建成功")
            
            self.player.set_media(media)
            logger.info(f"媒体对象设置成功")
            
            self._set_fullscreen()
            logger.info(f"设置全屏成功")
            
            result = self.player.play()
            logger.info(f"播放命令执行结果: {result}")
            
            self.current_media = media
            logger.info(f"开始播放本地视频: {file_path}")
            
            # 等待一段时间，检查播放状态
            time.sleep(1)
            state = self.player.get_state()
            logger.info(f"播放状态: {str(state)}")
            
            return True
        except Exception as e:
            logger.error(f"播放本地视频失败: {str(e)}")
            return False
    
    def play_nas(self, nas_path):
        """播放NAS视频"""
        try:
            # 停止当前播放
            self.stop()
            
            # NAS路径处理，假设是网络共享路径
            media = self.instance.media_new(nas_path)
            self.player.set_media(media)
            self._set_fullscreen()
            self.player.play()
            self.current_media = media
            logger.info(f"开始播放NAS视频: {nas_path}")
            return True
        except Exception as e:
            logger.error(f"播放NAS视频失败: {str(e)}")
            return False
    
    def play_live(self, live_url):
        """播放网络直播"""
        try:
            # 停止当前播放
            self.stop()
            
            # 检查是否是网页URL（更宽松的判断）
            if live_url.startswith('http') and ('.html' in live_url or 'tv.cctv.com' in live_url or 'live' in live_url):
                # 使用默认浏览器打开网页直播
                logger.info(f"识别为网页直播URL: {live_url}")
                self._open_web_browser(live_url)
                logger.info(f"开始播放网页直播: {live_url}")
            else:
                # 使用VLC播放网络流
                logger.info(f"识别为网络流URL: {live_url}")
                media = self.instance.media_new(live_url)
                self.player.set_media(media)
                self._set_fullscreen()
                result = self.player.play()
                logger.info(f"播放命令执行结果: {result}")
                self.current_media = media
                logger.info(f"开始播放网络直播流: {live_url}")
                
                # 等待一段时间，检查播放状态
                time.sleep(1)
                state = self.player.get_state()
                logger.info(f"播放状态: {str(state)}")
            return True
        except Exception as e:
            logger.error(f"播放网络直播失败: {str(e)}")
            return False
    
    def _open_web_browser(self, url):
        """打开网页浏览器播放直播"""
        try:
            # 使用默认浏览器打开URL，在服务器端执行
            logger.info(f"在服务器端打开浏览器播放: {url}")
            
            # 尝试使用Selenium控制浏览器
            try:
                from selenium import webdriver
                from selenium.webdriver.edge.options import Options
                from selenium.webdriver.common.by import By
                from selenium.webdriver.support.ui import WebDriverWait
                from selenium.webdriver.support import expected_conditions as EC
                import time
                
                # 配置Edge浏览器选项
                edge_options = Options()
                edge_options.add_argument('--start-fullscreen')
                edge_options.add_argument('--disable-notifications')
                edge_options.add_argument('--mute-audio')  # 可选：静音
                
                # 初始化浏览器驱动
                edge_path = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
                if os.path.exists(edge_path):
                    # 使用Edge浏览器
                    logger.info("使用Selenium打开Edge浏览器")
                    self.browser_driver = webdriver.Edge(options=edge_options)
                    self.browser_driver.get(url)
                    logger.info("浏览器打开成功，正在等待页面加载...")
                    
                    # 等待页面加载完成
                    time.sleep(3)
                    
                    # 尝试点击播放按钮（针对央视网）
                    try:
                        # 央视网直播页面的播放按钮
                        play_button = WebDriverWait(self.browser_driver, 10).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, 'button.play-btn'))
                        )
                        play_button.click()
                        logger.info("点击播放按钮成功")
                    except Exception as e:
                        logger.warning(f"未找到播放按钮或点击失败: {str(e)}")
                    
                    # 尝试进入全屏
                    try:
                        # 查找全屏按钮并点击
                        fullscreen_button = WebDriverWait(self.browser_driver, 10).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, 'button.fullscreen-btn'))
                        )
                        fullscreen_button.click()
                        logger.info("点击全屏按钮成功")
                    except Exception as e:
                        logger.warning(f"未找到全屏按钮或点击失败: {str(e)}")
                        # 尝试使用键盘快捷键进入全屏
                        try:
                            from selenium.webdriver.common.keys import Keys
                            self.browser_driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.F11)
                            logger.info("使用F11键进入全屏成功")
                        except Exception as e:
                            logger.warning(f"使用F11键进入全屏失败: {str(e)}")
                    
                    logger.info("浏览器操作完成")
                else:
                    # 回退到使用start命令
                    self.browser_process = subprocess.Popen(['start', url], shell=True)
                    logger.info("默认浏览器打开成功")
            except ImportError:
                logger.warning("Selenium未安装，使用默认方式打开浏览器")
                # 回退到使用start命令
                self.browser_process = subprocess.Popen(['start', url], shell=True)
                logger.info("默认浏览器打开成功")
        except Exception as e:
            logger.error(f"打开网页浏览器失败: {str(e)}")
    
    def stop(self):
        """停止播放"""
        try:
            # 停止VLC播放器
            if self.player:
                self.player.stop()
                self.current_media = None
            
            # 停止Selenium浏览器驱动
            if hasattr(self, 'browser_driver') and self.browser_driver:
                try:
                    logger.info("尝试关闭Selenium浏览器驱动")
                    self.browser_driver.quit()
                    logger.info("Selenium浏览器驱动关闭成功")
                    self.browser_driver = None
                except Exception as e:
                    logger.error(f"关闭Selenium浏览器驱动失败: {str(e)}")
            
            # 停止浏览器进程
            if self.browser_process:
                try:
                    logger.info("尝试停止浏览器进程")
                    # 尝试终止浏览器进程
                    self.browser_process.terminate()
                    # 等待进程结束
                    try:
                        self.browser_process.wait(timeout=5)
                        logger.info("浏览器进程已成功终止")
                    except subprocess.TimeoutExpired:
                        # 如果超时，强制杀死进程
                        logger.warning("浏览器进程终止超时，尝试强制杀死")
                        self.browser_process.kill()
                        logger.info("浏览器进程已强制杀死")
                except Exception as e:
                    logger.error(f"停止浏览器进程失败: {str(e)}")
                finally:
                    self.browser_process = None
            
            logger.info("停止播放")
            return True
        except Exception as e:
            logger.error(f"停止播放失败: {str(e)}")
            return False
    
    def pause(self):
        """暂停播放"""
        try:
            self.player.pause()
            logger.info("暂停播放")
            return True
        except Exception as e:
            logger.error(f"暂停播放失败: {str(e)}")
            return False
    
    def resume(self):
        """恢复播放"""
        try:
            self.player.play()
            logger.info("恢复播放")
            return True
        except Exception as e:
            logger.error(f"恢复播放失败: {str(e)}")
            return False
    
    def set_screen(self, screen_index):
        """设置播放屏幕"""
        try:
            self.screen_index = screen_index
            logger.info(f"设置播放屏幕: {screen_index}")
            return True
        except Exception as e:
            logger.error(f"设置屏幕失败: {str(e)}")
            return False
    
    def _set_fullscreen(self):
        """设置全屏"""
        try:
            self.player.set_fullscreen(True)
        except Exception as e:
            logger.error(f"设置全屏失败: {str(e)}")
    
    def get_status(self):
        """获取播放状态"""
        try:
            state = self.player.get_state()
            return {
                'state': str(state),
                'is_playing': state == vlc.State.Playing
            }
        except Exception as e:
            logger.error(f"获取播放状态失败: {str(e)}")
            return {'state': 'Error', 'is_playing': False}