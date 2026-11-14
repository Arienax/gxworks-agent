"""Small complete decisions for offline Design protocol tests."""


def latch_output():
    return [{
        "target": "运行输出", "state": "运行位保存启停状态。",
        "activation": "启动时置运行位。", "update": "停止优先，更新运行位后统一驱动输出。",
        "release": "停止时清除运行位并撤销输出。",
        "interruption": "按给定启停要求处理，不添加额外恢复条件。",
        "selection": "采用单个运行位，直接跟随按钮不能保持；逐输出设置保持位增加重复状态。",
    }]


def batch_output():
    return [{
        "target": "归档数据", "state": "当前采样批次和归档区。",
        "activation": "每批采样完成后归档。", "update": "连续字区批量复制，复制后发布完成标志。",
        "release": "下一批完成时更新归档区，不预设现场容量。",
        "interruption": "停止与恢复未给定，不增添工艺要求。",
        "selection": "选择批量复制代替逐字搬移，数据量相同，指令重复量减少，扫描仍需处理全部数据。",
    }]


def contact_output_en():
    return [{
        "target": "Y0", "state": "No persistent state.",
        "activation": "X0 is true.", "update": "Assign Y0 from X0 once each scan.",
        "release": "X0 is false; no records need releasing.",
        "interruption": "No pause or restart behavior requested; do not invent it.",
        "selection": "Use a direct contact. A latch adds state and changes the required output after X0 clears.",
    }]
