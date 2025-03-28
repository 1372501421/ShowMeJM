"""
对文件的下载与打包
"""
import glob
import os
import re
import shutil
import time
import yaml

import jmcomic
from PIL import Image
from pkg.platform.types import MessageChain
from pkg.plugin.context import EventContext
from plugins.ShowMeJM.utils.jm_options import JmOptions
from plugins.ShowMeJM.utils.jm_send_http_request import *


async def before_download(ctx: EventContext, options: JmOptions, manga_id):
    try:
        pdf_files = []
        try:
            pdf_files = download_and_get_pdf(options, manga_id)
        except Exception as e:
            await ctx.reply(MessageChain(["下载时出现问题:" + str(e)]))
        print(f"成功保存了{len(pdf_files)}个pdf")
        single_file_flag = len(pdf_files) == 1
        if len(pdf_files) > 0:
            await ctx.reply(MessageChain(["你寻找的本子已经打包发在路上啦, 即将送达~"]))
            if ctx.event.launcher_type == "person":
                await send_files_in_order(options, ctx, pdf_files, manga_id, single_file_flag, is_group=False)
            else:
                await send_files_in_order(options, ctx, pdf_files, manga_id, single_file_flag, is_group=True)
        else:
            print("没有找到下载的pdf文件")
            await ctx.reply(MessageChain(["没有找到下载的pdf文件"]))
    except Exception as e:
        await ctx.reply(MessageChain(["代码运行时出现问题:" + str(e)]))


# 下载图片
def download_and_get_pdf(options: JmOptions, arg):
    # 自定义设置：
    if os.path.exists(options.option):
        load_config = jmcomic.JmOption.from_file(options.option)
    else:
        raise Exception("未检测到JM下载的配置文件")
    album, dler = jmcomic.download_album(arg, load_config)
    downloaded_file_name = album.album_id
    with open(options.option, "r", encoding="utf8") as f:
        data = yaml.load(f, Loader=yaml.FullLoader)
        path = data["dir_rule"]["base_dir"]

    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_dir() and downloaded_file_name == entry.name:
                real_name = glob.escape(entry.name)
                pattern = f"{path}/{real_name}*.pdf"
                matches = glob.glob(pattern)
                if len(matches) > 0:
                    print(f"文件：《{entry.name}》 已存在无需转换pdf，直接返回")
                    return matches
                else:
                    print("开始转换：%s " % entry.name)
                    try:
                        return all2PDF(options, path + "/" + entry.name, path, entry.name)
                    except Exception as e:
                        print(f"转换pdf时发生错误: {str(e)}")
                        raise e
    return []


def all2PDF(options, input_folder, pdfpath, pdfname):
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

    # 自然顺序排序
    def natural_sort_key(entry):
        name = entry.name
        match = re.match(r"(\d+)", name)
        return int(match.group(1)) if match else name

    for i in zimulu:
        with os.scandir(path + "/" + str(i)) as entries:
            sorted_entries = sorted(entries, key=natural_sort_key)
            for entry in sorted_entries:
                if entry.is_dir():
                    print("这一级不应该有子目录")
                if entry.is_file():
                    image_paths.append(path + "/" + str(i) + "/" + entry.name)

    pdf_files = []
    # 分页处理
    i = 1
    pdf_page_size = options.pdf_max_pages if options.pdf_max_pages > 0 else len(image_paths)
    for page in range(0, len(image_paths), pdf_page_size):
        print(f"开始处理第{i}个pdf")
        trunk = image_paths[page: page + pdf_page_size]
        # 分批处理图像 减少内存占用
        temp_pdf = f"plugins/ShowMeJM/temp{pdfname}.pdf"
        if os.path.exists(temp_pdf):
            os.remove(temp_pdf)
        for j in range(0, len(trunk), options.batch_size):
            batch = trunk[j:j + options.batch_size]
            with Image.open(batch[0]) as first_img:
                if j == 0:
                    first_img.save(
                        temp_pdf,
                        save_all=True,
                        append_images=[Image.open(img) for img in batch[1:]]
                    )
                else:
                    first_img.save(
                        temp_pdf,
                        save_all=True,
                        append_images=[Image.open(img) for img in batch[1:]],
                        append=True
                    )
        output_pdf = os.path.join(pdfpath, f"{pdfname}-{i}.pdf")
        try:
            shutil.move(temp_pdf, output_pdf)
        except FileNotFoundError:
            print("源文件不存在")
            raise Exception("源文件不存在")
        except PermissionError:
            print("权限不足，无法移动文件")
            raise Exception("权限不足，无法移动文件")
        except Exception as e:
            print(f"发生错误: {e}")
            raise Exception(f"发生错误: {e}")
        pdf_files.append(output_pdf)
        i += 1

    end_time = time.time()
    run_time = end_time - start_time
    print("运行时间：%3.2f 秒" % run_time)
    return pdf_files


# 按顺序一个一个上传文件 方便阅读
async def send_files_in_order(options: JmOptions, ctx: EventContext, pdf_files, manga_id, single_file_flag, is_group):
    i = 0
    for pdf_path in pdf_files:
        if os.path.exists(pdf_path):
            i += 1
            suffix = '' if single_file_flag else f'-{i}'
            file_name = f"{manga_id}{suffix}.pdf"
            try:
                if is_group:
                    folder_id = await get_group_folder_id(options, ctx, ctx.event.launcher_id, options.group_folder)
                    await upload_group_file(options, ctx.event.launcher_id, folder_id, pdf_path, file_name)
                else:
                    await upload_private_file(options, ctx.event.sender_id, pdf_path, file_name)
                print(f"文件 {file_name} 已成功发送")
            except Exception as e:
                await ctx.reply(MessageChain([f"发送文件 {file_name} 时出错: {str(e)}"]))
                print(f"发送文件 {file_name} 时出错: {str(e)}")

# 获取群文件目录是否存在 并返回目录id
async def get_group_folder_id(options: JmOptions, ctx: EventContext, group_id, folder_name):
    if folder_name == '/':
        return '/'
    data = await get_group_root_files(options, group_id)
    for folder in data.get('folders', []):
        if folder.get('folder_name') == folder_name:
            return folder.get('folder_id')
    # 未找到该文件夹时创建文件夹
    folder_id = await create_group_file_folder(options, group_id, folder_name)
    if folder_id is None:
        data = await get_group_root_files(options, group_id)
        for folder in data.get('folders', []):
            if folder.get('folder_name') == folder_name:
                return folder.get('folder_id')
        return "/"
    return folder_id
