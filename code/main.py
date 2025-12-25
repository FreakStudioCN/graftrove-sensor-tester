# Python env   : MicroPython v1.23.0
# -*- coding: utf-8 -*-        
# @Time    : 2024/7/3 下午9:34
# @Author  : 李清水
# @File    : main.py
# @Description : I2C类实验，主要完成读取串口陀螺仪数据后显示在OLED屏幕上

# ======================================== 导入相关模块 ========================================

# 从SSD1306模块中导入SSD1306_I2C类
from ssd1306 import SSD1306_I2C
# 硬件相关的模块
from machine import I2C, Pin, Timer, UART, ADC, PWM
# 导入时间相关的模块
import time
# 系统相关的模块
import os
# 导入schedule调度器模块
import micropython
# 导入输入输出相关模块
import sys
import select

# ======================================== 全局变量 ============================================

# OLED屏幕地址
OLED_ADDRESS = 0

# 动画控制变量
# 移动文字X坐标（全局，供定时器和主循环访问）
text_x = 0
# 文字移动方向
text_dir = 1

# 定时器全局实例
tim = None
# OLED全局实例
oled = None

# 定时器计时相关（控制5秒后停止）
# 记录定时器启动的毫秒数
timer_start_ms = 0
# 定时器运行状态标志
timer_running = True

# 功能控制变量（保证功能互斥）
# 当前运行功能：None/adc/pwm/gpio/i2c/uart
current_func = None
# 功能实例（用于停止）
pwm_obj = None
uart_obj = None

# 中断防抖标志（避免按键抖动重复触发）
irq_flag = False

# ======================================== 功能函数 ============================================

# 该函数由schedule调度，在主线程执行（可安全操作OLED）
def update_text_display(arg):
    global text_x, text_dir, oled, timer_start_ms, timer_running, tim

    # 计算定时器已运行时间（毫秒），ticks_diff处理时间溢出问题
    elapsed_ms = time.ticks_diff(time.ticks_ms(), timer_start_ms)

    # 核心逻辑：1秒后停止定时器，文字固定在中间
    if elapsed_ms >= 1000 and timer_running:
        # 1. 停止定时器
        tim.deinit()
        timer_running = False
        print(f"Timer stopped after 5s (elapsed: {elapsed_ms}ms)")

        # 2. 计算文字中间位置：FreakStudio共10字符*8px=80px，(128-80)/2=24
        center_x = (128 - 8 * 10) // 2
        text_x = center_x

        # 3. 清空屏幕并绘制居中文字（固定显示）
        oled.fill(0)
        oled.text('FreakStudio', text_x, 10, 1)
        oled.show()
        # 终止本次函数执行，不再执行移动逻辑
        return

        # 未到5秒且定时器运行中，执行文字移动逻辑
    if timer_running:
        # 1. 计算文字移动坐标
        # 8px/字符 * 10个字符（FreakStudio）
        if text_x > 128 - 8 * 10:
            text_dir = -1
        elif text_x < 0:
            text_dir = 1
        text_x += text_dir

        # 2. 仅刷新移动文字区域（减少全屏清屏，降低闪屏）
        # 清空文字区域背景
        oled.fill_rect(0, 10, 128, 8, 0)
        # 绘制更新后的文字
        oled.text('FreakStudio', text_x, 10, 1)
        oled.show()

def move_text(timer):
    # 中断上下文：仅调度，不做硬件操作
    # schedule要求：函数只能有1个参数，且不能频繁调度（加简单判重）
    try:
        micropython.schedule(update_text_display, None)
    except RuntimeError:
        # 若上一次调度未完成，忽略本次（避免调度队列溢出）
        pass

def stop_current_func(arg):
    """
    停止当前运行的功能（保证互斥）
    参数arg：schedule调度要求的占位参数
    """
    global current_func, pwm_obj, uart_obj
    # 1. 停止PWM输出（若存在）
    if pwm_obj:
        pwm_obj.deinit()
        pwm_obj = None
    # 2. 释放UART实例（若存在）
    if uart_obj:
        uart_obj = None
    # 3. 清空OLED屏幕
    if oled:
        oled.fill(0)
        oled.show()
    # 4. 重置当前功能状态
    current_func = None

def reset_irq_flag(arg):
    """
    重置中断防抖标志（调度执行，避免中断内sleep）
    参数arg：schedule调度要求的占位参数
    """
    global irq_flag
    # 防抖延迟：100ms
    time.sleep_ms(100)
    irq_flag = False

def adc_func(arg):
    """
    AIN0按键触发：采集ADC0~ADC3电压并在OLED显示
    参数arg：schedule调度要求的占位参数
    ADC0=GP26, ADC1=GP27, ADC2=GP28, ADC3=GP29
    """
    global current_func, oled
    # 标记当前运行功能为ADC
    current_func = "adc"
    # 初始化ADC通道
    adc_list = [ADC(Pin(26)), ADC(Pin(27)), ADC(Pin(28)), ADC(Pin(29))]

    # 循环采集并显示（直到切换功能）
    while current_func == "adc":
        # 清空OLED缓存
        oled.fill(0)
        # 显示标题
        oled.text("ADC0~3 Voltage", 0, 0, 1)
        # 采集每路ADC电压并显示（3.3V参考，16位采样）
        for i, adc in enumerate(adc_list):
            # 计算电压：ADC值(0~65535) * 3.3V / 65535
            volt = adc.read_u16() * 3.3 / 65535
            # 显示格式：ADC0: 1.23V（每行y偏移12px，避免重叠）
            oled.text(f"ADC{i}: {volt:.2f}V", 0, 12 + i * 12, 1)
        # 刷新OLED显示
        oled.show()
        # 采集间隔：500ms
        time.sleep(0.5)

def pwm_func(arg):
    """
    DIO1按键触发：GP9生成1000Hz PWM，占空比10%~90%循环变化
    参数arg：schedule调度要求的占位参数
    PWM频率：1000Hz，占空比步长：1%，循环范围：10%~90%
    """
    global current_func, oled, pwm_obj
    # 标记当前运行功能为PWM
    current_func = "pwm"
    # 初始化PWM（GP9引脚，1000Hz频率）
    pwm_obj = PWM(Pin(9))
    pwm_obj.freq(1000)
    # 初始化占空比：10%
    duty = 10
    # 占空比步长：1%
    step = 5
    # 占空比最大值/最小值
    max_duty = 90
    min_duty = 10

    # 循环更新PWM占空比并显示（直到切换功能）
    while current_func == "pwm":
        # 清空OLED缓存
        oled.fill(0)
        # 显示标题
        oled.text("PWM (GP9) Status", 0, 0, 1)
        # 显示频率
        oled.text(f"Freq: 1000Hz", 0, 15, 1)
        # 显示当前占空比
        oled.text(f"Duty: {duty}%", 0, 30, 1)
        # 刷新OLED显示
        oled.show()
        # 设置PWM占空比（0~65535对应0~100%）
        pwm_obj.duty_u16(int(duty / 100 * 65535))
        # 更新占空比
        duty += step
        # 占空比达到边界则反向
        if duty >= max_duty or duty <= min_duty:
            step *= -1
        # 刷新间隔：100ms
        time.sleep(0.1)

def gpio_func(arg):
    """
    DIO0按键触发：读取GP7电平并在OLED显示
    参数arg：schedule调度要求的占位参数
    GP7配置为上拉输入，显示电平状态：HIGH(1)/LOW(0)
    """
    global current_func, oled
    # 标记当前运行功能为GPIO
    current_func = "gpio"
    # 初始化GP7为上拉输入
    gp7 = Pin(7, Pin.IN, Pin.PULL_UP)

    # 循环读取并显示（直到切换功能）
    while current_func == "gpio":
        # 清空OLED缓存
        oled.fill(0)
        oled.show()
        # 显示标题
        oled.text("GP7 Level", 0, 0, 1)
        # 读取GP7电平
        level = gp7.value()
        # 转换为易读字符串
        level_str = "HIGH (1)" if level else "LOW (0)"
        # 显示电平状态
        oled.text(f"Status: {level_str}", 0, 20, 1)
        # 刷新OLED显示
        oled.show()
        # 刷新间隔：200ms
        time.sleep(0.2)

def i2c_scan_func(arg):
    """
    I2C1按键触发：扫描GP2(SDA)/GP3(SCL)上的I2C从机地址并显示
    参数arg：schedule调度要求的占位参数
    I2C1配置：频率100KHz，GP2=SDA，GP3=SCL
    特性：循环扫描+try-except异常捕获，避免扫描出错崩溃
    """
    global current_func, oled
    # 标记当前运行功能为I2C扫描
    current_func = "i2c"

    # 初始化I2C1（ID=1，GP2=SDA，GP3=SCL，速率100KHz）
    try:
        i2c_scan = I2C(1, sda=Pin(2), scl=Pin(3), freq=100000)
        print("I2C1 init success (100KHz)")
    except Exception as init_err:
        # I2C初始化失败处理
        oled.fill(0)
        oled.text("I2C Init Error", 0, 0, 1)
        oled.text(f"Err: {str(init_err)[:10]}", 0, 20, 1)
        oled.show()
        # 保持显示直到切换功能
        while current_func == "i2c":
            time.sleep(0.5)
        return

    # 循环扫描I2C地址（直到切换功能）
    while current_func == "i2c":
        try:
            # 清空OLED缓存
            oled.fill(0)
            oled.show()
            # 显示标题（标注100KHz速率）
            oled.text("I2C (GP2/GP3) Scan", 0, 0, 1)
            oled.text("Rate: 100KHz", 0, 10, 1)

            # 尝试扫描I2C从机设备（核心扫描逻辑）
            devices = i2c_scan.scan()

            # 处理扫描结果
            if not devices:
                # 无设备连接
                oled.text("No Device Found", 0, 20, 1)
            else:
                # 显示找到的设备数量
                oled.text(f"Found: {len(devices)} Dev", 0, 20, 1)
                # 显示前6个设备地址（避免超出OLED高度）
                for i, addr in enumerate(devices[:6]):
                    # 十六进制显示地址，每行y偏移8px
                    oled.text(f"Addr{i}: 0x{addr:02x}", 0, 35 + i * 8, 1)

            # 刷新OLED显示
            oled.show()

        except Exception as scan_err:
            # 扫描出错时的异常处理
            oled.fill(0)
            oled.text("I2C Scan Error", 0, 0, 1)
            # 截取错误信息前10位，避免OLED显示溢出
            oled.text(f"Err: {str(scan_err)[:10]}", 0, 20, 1)
            oled.show()
            print(f"I2C scan error: {scan_err}")

        # 扫描间隔：500ms（可根据需求调整）
        time.sleep(0.5)

def uart_func(arg):
    """
    UART按键触发：终端配置+双向透传（OLED仅显示极简提示）
    参数arg：schedule调度要求的占位参数
    核心功能：
    1. OLED仅显示：UART RW → Terminal
    2. 终端配置：波特率/超时（默认9600/100ms）
    3. 双向透传：串口↔终端，输入'exit'退出
    """
    global current_func, oled, uart_obj
    # 标记当前运行功能为UART
    current_func = "uart"

    # ====================== 第一步：OLED仅显示极简提示 ======================
    oled.fill(0)
    # 简写提示：UART RW → Terminal（适配128x64，居中显示）
    oled.text("UART RW Terminal", 5, 25, 1)
    oled.show()

    # ====================== 第二步：终端交互配置串口参数 ======================
    try:
        # 终端提示用户输入配置（带默认值+容错）
        print("\n===== UART配置 =====")
        # 1. 波特率配置（默认9600，限定常见值）
        while True:
            baud_input = input("波特率（默认9600）：").strip()
            if not baud_input:
                baudrate = 9600
                break
            try:
                baudrate = int(baud_input)
                if baudrate in [1200, 2400, 4800, 9600, 19200, 38400, 115200]:
                    break
                print("⚠️  请输入常见值：1200/2400/4800/9600/19200/38400/115200")
            except ValueError:
                print("⚠️  请输入数字！")

        # 2. 超时配置（默认100ms，限定10~1000）
        while True:
            timeout_input = input("超时(ms，默认100)：").strip()
            if not timeout_input:
                timeout = 100
                break
            try:
                timeout = int(timeout_input)
                if 10 <= timeout <= 1000:
                    break
                print("⚠️  超时范围10~1000ms！")
            except ValueError:
                print("⚠️  请输入数字！")

        # 初始化UART（MicroPython标准写法，无关键字参数）
        uart_obj = UART(0, baudrate, tx=Pin(0), rx=Pin(1), timeout=timeout)
        print(f"\n✅ UART初始化成功：{baudrate}bps | 超时{timeout}ms")
        print("===== 双向透传开始 =====")
        print("💡 终端输入 → 串口发送 | 串口接收 → 终端显示")
        print("💡 输入'exit'退出UART功能\n")

    except Exception as init_err:
        # 配置/初始化失败处理
        oled.fill(0)
        oled.text("UART Config Err", 10, 25, 1)
        oled.show()
        print(f"\n❌ UART配置失败：{init_err}")
        # 保持提示直到切换功能
        while current_func == "uart":
            time.sleep(0.5)
        return

    # ====================== 第三步：双向透传主循环（核心） ======================
    while current_func == "uart":
        try:
            # ---------- 1. 串口→终端：完整16进制+优化字符显示 ----------
            if uart_obj.any():
                # 读取所有可用字节（-1表示读取全部，避免数据截断）
                raw_data = uart_obj.read(-1)
                if raw_data and len(raw_data) > 0:
                    # ① 16进制转换：两位大写，空格分隔（完整无截断）
                    hex_str = ' '.join([f"{b:02X}" for b in raw_data])
                    # ② 字符转换：不可打印字符用"."替代
                    char_str = ''
                    for b in raw_data:
                        if 32 <= b <= 126:  # 可打印ASCII范围
                            char_str += chr(b)
                        else:
                            char_str += '.'
                    # ③ 终端完整输出（分两行显示，避免混乱）
                    print(f"[串口接收]")
                    print(f"  16进制：{hex_str}")
                    print(f"  字符版：{char_str}\n")

            # ---------- 2. 终端→串口：支持16进制/字符发送 ----------
            if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                terminal_input = sys.stdin.readline().strip()
                if terminal_input:
                    if terminal_input.lower() == 'exit':
                        print("\n📤 退出UART双向透传")
                        current_func = None
                        break
                    # 终端输入转发到串口（保留原始输入）
                    uart_obj.write(terminal_input + '\n')
                    print(f"[终端发送] {terminal_input}\n")

        except Exception as trans_err:
            print(f"\n⚠️  UART透传错误：{trans_err}")
            time.sleep(0.5)

    # ====================== 退出清理 ======================
    oled.fill(0)
    oled.text("UART Closed", 10, 25, 1)
    oled.show()
    if uart_obj:
        uart_obj.deinit()

def ain0_irq_cb(pin):
    """
    AIN0按键（GP11）中断回调：仅调度，不执行耗时操作
    参数pin：中断触发的引脚对象（系统自动传入）
    """
    global irq_flag
    # 防抖判断：短时间内仅响应一次
    if irq_flag:
        return
    # 标记防抖状态为已触发
    irq_flag = True

    try:
        # 1. 调度停止当前功能（耗时操作放主线程）
        micropython.schedule(stop_current_func, None)
        # 2. 调度ADC功能（耗时操作放主线程）
        micropython.schedule(adc_func, None)
        # 3. 调度防抖复位（避免中断内sleep）
        micropython.schedule(reset_irq_flag, None)
        # 打印调试信息
        print("AIN0 key pressed: start ADC0~3 voltage collect")
    except Exception as e:
        print(f"AIN0 irq error: {e}")
        # 出错时重置防抖标志
        micropython.schedule(reset_irq_flag, None)

def dio1_irq_cb(pin):
    """
    DIO1按键（GP12）中断回调：仅调度，不执行耗时操作
    参数pin：中断触发的引脚对象（系统自动传入）
    """
    global irq_flag
    if irq_flag:
        return
    irq_flag = True

    try:
        micropython.schedule(stop_current_func, None)
        micropython.schedule(pwm_func, None)
        micropython.schedule(reset_irq_flag, None)
        print("DIO1 key pressed: start GP9 PWM generate")
    except Exception as e:
        print(f"DIO1 irq error: {e}")
        micropython.schedule(reset_irq_flag, None)

def dio0_irq_cb(pin):
    """
    DIO0按键（GP13）中断回调：仅调度，不执行耗时操作
    参数pin：中断触发的引脚对象（系统自动传入）
    """
    global irq_flag
    if irq_flag:
        return
    irq_flag = True

    try:
        micropython.schedule(stop_current_func, None)
        micropython.schedule(gpio_func, None)
        micropython.schedule(reset_irq_flag, None)
        print("DIO0 key pressed: start GP7 level read")
    except Exception as e:
        print(f"DIO0 irq error: {e}")
        micropython.schedule(reset_irq_flag, None)

def i2c1_irq_cb(pin):
    """
    I2C1按键（GP14）中断回调：仅调度，不执行耗时操作
    参数pin：中断触发的引脚对象（系统自动传入）
    """
    global irq_flag
    if irq_flag:
        return
    irq_flag = True

    try:
        micropython.schedule(stop_current_func, None)
        micropython.schedule(i2c_scan_func, None)
        micropython.schedule(reset_irq_flag, None)
        print("I2C1 key pressed: start I2C slave scan")
    except Exception as e:
        print(f"I2C1 irq error: {e}")
        micropython.schedule(reset_irq_flag, None)

def uart_irq_cb(pin):
    """
    UART按键（GP15）中断回调：仅调度，不执行耗时操作
    参数pin：中断触发的引脚对象（系统自动传入）
    """
    global irq_flag
    if irq_flag:
        return
    irq_flag = True

    try:
        micropython.schedule(stop_current_func, None)
        micropython.schedule(uart_func, None)
        micropython.schedule(reset_irq_flag, None)
        print("UART key pressed: start UART data read")
    except Exception as e:
        print(f"UART irq error: {e}")
        micropython.schedule(reset_irq_flag, None)

# ======================================== 自定义类 ============================================

# ======================================== 初始化配置 ==========================================

# 延时3s等待设备上电完毕
time.sleep(3)
# 打印调试消息
print("FreakStudio: Testing OLED display")

# 初始化按键（上拉输入 + 独立中断回调）
# AIN0按键：GP11，下降沿触发，ADC功能
ain0_key = Pin(11, Pin.IN, Pin.PULL_UP)
ain0_key.irq(trigger=Pin.IRQ_FALLING, handler=ain0_irq_cb)
# DIO1按键：GP12，下降沿触发，PWM功能
dio1_key = Pin(12, Pin.IN, Pin.PULL_UP)
dio1_key.irq(trigger=Pin.IRQ_FALLING, handler=dio1_irq_cb)
# DIO0按键：GP13，下降沿触发，GPIO电平功能
dio0_key = Pin(13, Pin.IN, Pin.PULL_UP)
dio0_key.irq(trigger=Pin.IRQ_FALLING, handler=dio0_irq_cb)
# I2C1按键：GP14，下降沿触发，I2C扫描功能
i2c1_key = Pin(14, Pin.IN, Pin.PULL_UP)
i2c1_key.irq(trigger=Pin.IRQ_FALLING, handler=i2c1_irq_cb)
# UART按键：GP15，下降沿触发，UART读取功能
uart_key = Pin(15, Pin.IN, Pin.PULL_UP)
uart_key.irq(trigger=Pin.IRQ_FALLING, handler=uart_irq_cb)

# 创建硬件I2C的实例，使用I2C1外设，时钟频率为400KHz，SDA引脚为6，SCL引脚为7
i2c = I2C(id=0, sda=Pin(4), scl=Pin(5), freq=400000)

# 输出当前目录下所有文件
print('START LIST ALL FILES')
for file in os.listdir():
    print('file name:', file)

# 开始扫描I2C总线上的设备，返回从机地址的列表
devices_list = i2c.scan()
print('START I2C SCANNER')

# 若devices_list为空，则没有设备连接到I2C总线上
if len(devices_list) == 0:
    print("No i2c device !")
# 若非空，则打印从机设备地址
else:
    print('i2c devices found:', len(devices_list))
    # 遍历从机设备地址列表
    for device in devices_list:
        print("I2C hexadecimal address: ", hex(device))
        if device == 0x3c or device == 0x3d:
            OLED_ADDRESS = device

# 创建SSD1306 OLED屏幕的实例，宽度为128像素，高度为64像素，不使用外部电源
oled = SSD1306_I2C(i2c, OLED_ADDRESS, 128, 64, False)
# 打印提示信息
print('OLED init success')

# (0,0)原点位置为屏幕左上角，右边为x轴正方向，下边为y轴正方向
# 绘制矩形外框
oled.rect(0, 0, 128, 64, 1)
# 显示文本
oled.text('Freak', 45, 5)
oled.text('Studio', 42, 15)
oled.text('Graftrove', 30, 25)
# 显示图像
oled.show()

time.sleep(1)

# 清除屏幕
oled.fill(0)
oled.show()

# 初始化软件定时器（核心：调度move_text函数）
# Timer(-1) = 软件定时器，period=50ms（周期，越小移动越快），mode=周期执行，callback=回调函数
tim = Timer(-1)
tim.init(period=50, mode=Timer.PERIODIC, callback=move_text)
# 记录定时器启动的毫秒数
timer_start_ms = time.ticks_ms()
# 标记定时器运行状态
timer_running = True
print("Timer started, will stop after 1s")

# ========================================  主程序  ============================================

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    # 停止动画定时器
    tim.deinit()
    # 停止当前功能
    stop_current_func(None)
    # 清空OLED屏幕
    oled.fill(0)
    oled.show()
    # 延时1秒
    time.sleep(1)
    # 显示退出成功提示
    oled.text('Exit Success!', 25, 30, 1)
    oled.show()