from pkg.platform.types import File
from pkg.plugin.context import register, handler, BasePlugin, APIHost, EventContext
from pkg.plugin.events import *  # 导入事件类
import re
import jmcomic, os, time, yaml
from PIL import Image
import json
import time
import glob
import aiohttp


# 注册插件
@register(name="ShowMeJM", description="jm下载", version="1.0", author="exneverbur")
class MyPlugin(BasePlugin):
    # napcat的域名和端口号
    # 使用时需在napcat内配置http服务器 host和port对应好
    http_host = "172.17.0.12"
    http_port = 3000
    # 打包成pdf时每批处理的图片数量 每批越小占用越小
    batch_size = 10
    # 每个pdf中最多有多少个图片 设置为0则不限制
    pdf_max_pages = 100
    # 上传到群文件的哪个目录?默认"/"是传到根目录 如果指定目录要提前在群文件里建好文件夹
    group_folder = "/"
    # 是否开启自动匹配消息中的jm号功能(消息中的所有数字加起来是6~7位数字就触发下载本子) 此功能可能会下载很多不需要的本子占据硬盘, 请谨慎开启
    auto_find_jm = True
    # 如果成功找到本子是否停止触发其他插件(Ture:若找到本子则后续其他插件不会触发)
    prevent_default = True


    # 插件加载时触发
    def __init__(self, host: APIHost):
        super().__init__(host)

    # 异步初始化
    async def initialize(self):
        pass

    @handler(PersonNormalMessageReceived)
    @handler(GroupNormalMessageReceived)
    async def message_received(self, ctx: EventContext):
        print("收到消息:" + ctx.event.text_message)
        receive_text = ctx.event.text_message
        cleaned_text = re.sub(r'@\S+\s*', '', receive_text).strip()
        print(f"原始消息: {receive_text}")
        print(f"清理后的消息: {cleaned_text}")
        if cleaned_text.startswith('jm'):  # 检查是否为命令
            parts = cleaned_text.split(' ', 1)  # 分割命令和参数
            command = parts[0]
            if len(parts) > 1:
                args = parts[1]
            else:
                return ctx.add_return("reply", ["请指定jm号"])
            print("接收指令:", command, "参数：", args)
            await ctx.reply(f"即将开始下载{args}, 请稍后...")
            await self.before_download(ctx, args)
        # 匹配消息中包含的 6~7 位数字
        elif self.auto_find_jm:
            numbers = re.findall(r'\d+', cleaned_text)
            concatenated_numbers = ''.join(numbers)
            if 6 <= len(concatenated_numbers) <= 7:
                await ctx.reply(f"你提到了{concatenated_numbers}...对吧?")
                await self.before_download(ctx, concatenated_numbers)

    # 插件卸载时触发
    def __del__(self):
        pass

    async def before_download(self, ctx: EventContext, manga_id):
        try:
            pdf_files = []
            try:
                pdf_files = self.download_and_get_pdf(manga_id)
            except Exception as e:
                ctx.add_return("reply", ["下载时出现问题:" + str(e)])
            print(f"成功保存了{len(pdf_files)}个pdf")
            single_file_flag = len(pdf_files) == 1
            if len(pdf_files) > 0:
                await ctx.reply("你寻找的本子已经打包发在路上啦, 即将送达~")
                print(pdf_files)
                if ctx.event.launcher_type == "person":
                    await self.send_files_in_order(ctx, pdf_files, manga_id, single_file_flag, is_group=False)
                else:
                    await self.send_files_in_order(ctx, pdf_files, manga_id, single_file_flag, is_group=True)
                if self.prevent_default:
                    # 阻止该事件默认行为（向接口获取回复）
                    ctx.prevent_default()
            else:
                print("没有找到下载的pdf文件")
                ctx.add_return("reply", ["没有找到下载的pdf文件"])
        except Exception as e:
            ctx.add_return("reply", ["代码运行时出现问题:" + str(e)])

    # 下载图片
    def download_and_get_pdf(self, arg):
        # 自定义设置：
        config = "plugins/ShowMeJM/config.yml"
        load_config = jmcomic.JmOption.from_file(config)
        ids = [arg]
        downloaded_file_name = ''
        for manhua in ids:
            album, dler = jmcomic.download_album(manhua, load_config)
            downloaded_file_name = album.name

        with open(config, "r", encoding="utf8") as f:
            data = yaml.load(f, Loader=yaml.FullLoader)
            path = data["dir_rule"]["base_dir"]

        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir() and downloaded_file_name == entry.name:
                    pattern = f"{path}/{entry.name}*.pdf"
                    matches = glob.glob(pattern)
                    if len(matches) > 0:
                        print(f"文件：《{entry.name}》 已存在无需转换pdf，直接返回")
                        return matches
                    else:
                        print("开始转换：%s " % entry.name)
                        return self.all2PDF(path + "/" + entry.name, path, entry.name)

    def all2PDF(self, input_folder, pdfpath, pdfname):
        start_time = time.time()
        path = input_folder
        zimulu = []  # 子目录（里面为image）
        image_paths = []  # 子目录图集

        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir():
                    zimulu.append(int(entry.name))
        # 对数字进行排序
        zimulu.sort()

        for i in zimulu:
            with os.scandir(path + "/" + str(i)) as entries:
                for entry in entries:
                    if entry.is_dir():
                        print("这一级不应该有子目录")
                    if entry.is_file():
                        image_paths.append(path + "/" + str(i) + "/" + entry.name)

        pdf_files = []
        # 分页处理
        i = 1
        pdf_page_size = self.pdf_max_pages if self.pdf_max_pages > 0 else len(image_paths)
        for page in range(0, len(image_paths), pdf_page_size):
            print(f"开始处理第{i}个pdf")
            i += 1
            chunk = image_paths[page:page + pdf_page_size]
            output_pdf = os.path.join(pdfpath, f"{pdfname}-{page // pdf_page_size}.pdf")
            # 分批次写入
            with Image.open(chunk[0]) as first_img:
                first_img.save(
                    output_pdf,
                    save_all=True,
                    append_images=[Image.open(img) for img in chunk[1:]]
                )
            pdf_files.append(output_pdf)

        end_time = time.time()
        run_time = end_time - start_time
        print("运行时间：%3.2f 秒" % run_time)
        return pdf_files

    # 按顺序一个一个上传文件 方便阅读
    async def send_files_in_order(self, ctx: EventContext, pdf_files, manga_id, single_file_flag, is_group):
        i = 0
        for pdf_path in pdf_files:
            if os.path.exists(pdf_path):
                i += 1
                suffix = '' if single_file_flag else f'-{i}'
                file_name = f"{manga_id}{suffix}.pdf"
                try:
                    if is_group:
                        await self.upload_group_file(ctx.event.launcher_id, pdf_path, file_name)
                    else:
                        await self.upload_private_file(ctx.event.sender_id, pdf_path, file_name)
                    print(f"文件 {file_name} 已成功发送")
                except Exception as e:
                    ctx.add_return("reply", [f"发送文件 {file_name} 时出错: {str(e)}"])
                    print(f"发送文件 {file_name} 时出错: {str(e)}")

    async def upload_private_file(self, user_id, file, name):
        url = f"http://{self.http_host}:{self.http_port}/upload_private_file"
        payload = {
            "user_id": user_id,
            "file": file,
            "name": name
        }
        headers = {
            'Content-Type': 'application/json'
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    raise Exception(f"上传失败，状态码: {response.status}, 错误信息: {response.message}")

    async def upload_group_file(self, group_id, file, name):
        url = f"http://{self.http_host}:{self.http_port}/upload_group_file"
        payload = {
            "group_id": group_id,
            "file": file,
            "name": name,
            "folder_id": self.group_folder
        }
        headers = {
            'Content-Type': 'application/json'
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    raise Exception(f"上传失败，状态码: {response.status}, 错误信息: {response.message}")

