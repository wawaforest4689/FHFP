
from dataclasses import dataclass, field
import re
from typing import Optional,Tuple
from datetime import datetime
from nicegui import ui
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
# 支持上级目录py脚本的类和函数搜索
# sys.path.append("../../")
from frontend.core import AppState,logger


@dataclass
class UserAccount:
    """用户账户实体"""
    user_id: str
    username: str  # 登录账号（唯一）
    phone: Optional[str]  # 手机号（唯一，可选）
    password_hash: str  # 密码哈希（生产环境应使用 bcrypt）
    nickname: str
    avatar_url: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class AuthValidator:
    """
    认证校验器（静态工具类）

    密码规则：
      - 长度 >= 8 位
      - 必须包含英文字母（a-z, A-Z）
      - 必须包含数字（0-9）
      - 必须包含特殊字符（!@#$%^&*()_+-=[]{}|;:,.<>?）
    """

    PASSWORD_PATTERN = re.compile(
        r'^(?=.*[A-Za-z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]).{8,}$'
    )

    PHONE_PATTERN = re.compile(r'^1[3-9]\d{9}$')

    @classmethod
    def validate_password(cls, password: str) -> Tuple[bool, str]:
        """
        校验密码强度

        Returns:
            Tuple[bool, str]: (是否通过, 错误信息)
        """
        if len(password) < 8:
            return False, "密码长度至少8位"
        if not re.search(r'[A-Za-z]', password):
            return False, "密码必须包含英文字母"
        if not re.search(r'\d', password):
            return False, "密码必须包含数字"
        if not re.search(r'[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]', password):
            return False, "密码必须包含特殊字符"
        return True, ""

    @classmethod
    def validate_phone(cls, phone: str) -> Tuple[bool, str]:
        if not phone:
            return True, ""  # 手机号可选
        if not cls.PHONE_PATTERN.match(phone):
            return False, "手机号格式不正确"
        return True, ""

    @classmethod
    def validate_username(cls, username: str) -> Tuple[bool, str]:
        if not username or len(username) < 3:
            return False, "账号长度至少3位"
        if not re.match(r'^[A-Za-z0-9_\-]+$', username):
            return False, "账号只能包含字母、数字、下划线和横线"
        return True, ""


# ==============================================================================
# 扩展的 MockBackendAPI（支持注册、查重、密码校验）
# ==============================================================================




class AuthDialog:
    """
    统一认证对话框（登录 + 注册切换）

    特性：
      - 密码实时强度校验（长度、字母、数字、特殊字符）
      - 账号/手机号实时查重（防抖）
      - 登录支持账号或手机号
    """

    def __init__(self, state: AppState, mode: str = "login"):
        """
        Args:
            mode (str): "login" | "register"
        """
        self.state = state
        self.mode = mode  # 当前模式
        self.dialog = ui.dialog()
        self._build()

    def _build(self) -> None:
        with self.dialog, ui.card().classes('fhfp-card w-[420px] p-6'):
            # 标题切换
            self.title_label = ui.label('欢迎回来').classes('text-2xl font-bold text-white mb-1')
            self.subtitle_label = ui.label('登录以保存您的创作进度').classes('text-sm text-slate-400 mb-6')

            # 账号输入（支持账号或手机号登录）
            self.inp_account = ui.input('账号 / 手机号') \
                .classes('w-full mb-3') \
                .props('outlined')

            # 手机号（仅注册显示）
            self.inp_phone = ui.input('手机号（可选）') \
                .classes('w-full mb-3') \
                .props('outlined') \
                .bind_visibility_from(self, 'mode', lambda m: m == 'register')

            # 昵称（仅注册显示）
            self.inp_nickname = ui.input('昵称') \
                .classes('w-full mb-3') \
                .props('outlined') \
                .bind_visibility_from(self, 'mode', lambda m: m == 'register')

            # 密码
            self.inp_password = ui.input('密码', password=True) \
                .classes('w-full mb-2') \
                .props('outlined')

            # 密码强度指示器（仅注册显示）
            with ui.column().classes('w-full mb-4').bind_visibility_from(self, 'mode', lambda m: m == 'register'):
                self.pwd_strength_bar = ui.linear_progress(value=0, show_value=False) \
                    .classes('w-full') \
                    .props('color=red')
                self.pwd_hint = ui.label('密码需至少8位，包含字母、数字和特殊字符') \
                    .classes('text-xs text-slate-500')

            # 确认密码（仅注册显示）
            self.inp_confirm = ui.input('确认密码', password=True) \
                .classes('w-full mb-4') \
                .props('outlined') \
                .bind_visibility_from(self, 'mode', lambda m: m == 'register')

            # 操作按钮
            with ui.row().classes('w-full justify-end gap-2'):
                self.toggle_btn = ui.button('去注册', on_click=self._toggle_mode).props('flat color=grey')
                self.action_btn = ui.button('登录', on_click=self._do_action).classes(
                    'bg-green-500 text-black font-bold px-6')

            # 错误提示
            self.error_label = ui.label('').classes('text-red-400 text-sm mt-2 w-full text-center')

    def open(self) -> None:
        self.dialog.open()

    def _toggle_mode(self) -> None:
        """切换登录/注册模式"""
        if self.mode == 'login':
            self.mode = 'register'
            self.title_label.set_text('创建账号')
            self.subtitle_label.set_text('注册以开始您的创作之旅')
            self.action_btn.set_text('注册')
            self.toggle_btn.set_text('已有账号？去登录')
        else:
            self.mode = 'login'
            self.title_label.set_text('欢迎回来')
            self.subtitle_label.set_text('登录以保存您的创作进度')
            self.action_btn.set_text('登录')
            self.toggle_btn.set_text('去注册')
        self.error_label.set_text('')

    def _update_password_strength(self, password: str) -> None:
        """实时更新密码强度条"""
        score = 0
        if len(password) >= 8: score += 25
        if re.search(r'[A-Za-z]', password): score += 25
        if re.search(r'\d', password): score += 25
        if re.search(r'[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]', password): score += 25

        self.pwd_strength_bar.value = score / 100
        if score <= 25:
            self.pwd_strength_bar.props('color=red')
            self.pwd_hint.set_text('密码强度：弱')
            self.pwd_hint.classes('text-red-400', remove='text-yellow-400 text-green-400')
        elif score <= 75:
            self.pwd_strength_bar.props('color=yellow')
            self.pwd_hint.set_text('密码强度：中')
            self.pwd_hint.classes('text-yellow-400', remove='text-red-400 text-green-400')
        else:
            self.pwd_strength_bar.props('color=green')
            self.pwd_hint.set_text('密码强度：强')
            self.pwd_hint.classes('text-green-400', remove='text-red-400 text-yellow-400')

    async def _do_action(self) -> None:
        """执行登录或注册"""
        self.error_label.set_text('')

        account = self.inp_account.value.strip()
        password = self.inp_password.value

        if not account or not password:
            self.error_label.set_text('请填写账号和密码')
            return

        if self.mode == 'register':
            # 注册流程
            # 1. 密码强度二次校验
            ok, msg = AuthValidator.validate_password(password)
            if not ok:
                self.error_label.set_text(msg)
                return

            # 2. 确认密码
            if password != self.inp_confirm.value:
                self.error_label.set_text('两次输入的密码不一致')
                return

            phone = self.inp_phone.value.strip() or None
            nickname = self.inp_nickname.value.strip() or None

            # 3. 调用后端注册
            try:
                resp = await self.state.backend.register(
                    username=account,
                    password=password,
                    phone=phone,
                    nickname=nickname
                )
                if resp.get("success"):
                    ui.notify(f"注册成功！欢迎，{resp.get('nickname', account)}", type="positive")
                    # 自动切换到登录
                    self._toggle_mode()
                    self.error_label.set_text('注册成功，请登录')
                else:
                    self.error_label.set_text(resp.get("message", "注册失败"))
            except Exception as e:
                logger.error(f"Register error: {e}")
                self.error_label.set_text('网络错误，请稍后重试')

        else:
            # 登录流程
            try:
                resp = await self.state.backend.authenticate(account, password)
                if resp.get("success"):
                    self.state.set_user(resp)
                    ui.notify(f"欢迎回来，{resp.get('nickname', '用户')}!", type="positive")
                    self.dialog.close()
                    ui.navigate.to('/')
                else:
                    self.error_label.set_text(resp.get("message", "登录失败"))
            except Exception as e:
                logger.error(f"Login error: {e}")
                self.error_label.set_text('网络错误，请稍后重试')



