import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from frontend.core import AppState,APP_TITLE
from frontend.components.authdialog import AuthDialog
from nicegui import ui


class NavigationBar:
    def __init__(self, state: AppState):
        self.state = state
        self._build()

    def _build(self) -> None:
        with ui.header().classes('fhfp-nav w-full flex items-center justify-between px-6 py-3 fixed top-0 z-50'):
            with ui.row().classes('items-center gap-2 cursor-pointer').on('click', lambda: ui.navigate.to('/')):
                # ui.icon('spa', size='28px').classes('text-green-400')
                ui.image('/static/logo.png').classes('w-8 h-8')
                ui.label(APP_TITLE).classes('text-lg font-bold text-white tracking-wide')
            with ui.row().classes('gap-6 hidden md:flex'):
                ui.button('首页', on_click=lambda: ui.navigate.to('/')).props('flat color=white').classes(
                    'text-sm font-medium')
                ui.button('案例库', on_click=lambda: ui.navigate.to('/cases')).props('flat color=white').classes(
                    'text-sm font-medium')
                ui.button('个人作品', on_click=lambda: ui.navigate.to('/personal')).props('flat color=white').classes(
                    'text-sm font-medium')
            with ui.row().classes('items-center gap-3'):
                if self.state.is_authenticated():
                    ui.button('开始创作', on_click=lambda: ui.navigate.to('/create')).classes(
                        'bg-green-500 text-black text-xs font-bold px-4 py-2 rounded')
                    with ui.button(icon='account_circle').props('flat color=white round'):
                        with ui.menu().classes('bg-slate-800 shadow-xl'):
                            ui.menu_item('我的主页', lambda: ui.navigate.to('/personal'))
                            ui.menu_item('退出登录', self._handle_logout)
                else:
                    ui.button('登录', on_click=self._show_login).props('flat color=white')
                    ui.button('开始创作', on_click=lambda: ui.navigate.to('/create')).classes(
                        'bg-green-500 text-black text-xs font-bold px-4 py-2 rounded')

    def _show_login(self) -> None:
        AuthDialog(self.state).open()

    def _handle_logout(self) -> None:
        self.state.logout()
        ui.notify("已退出登录", type="info")
        ui.navigate.to('/')