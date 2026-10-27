# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""GX Works2 1.635 operational text, aligned by native resource identity.

Static vocabulary does not grant model/language runtime acceptance.
"""

LANGUAGES = ('zh-CN', 'en', 'ja')

TEXT = {'options_project': ('工程', 'Project', 'プロジェクト'),
 'monitor_online_menu': ('在线(O)', 'Online', 'オンライン(O)'),
 'monitor_submenu': ('监视(M)', 'Monitor', 'モニタ(M)'),
 'monitor_stop_all': ('监视停止(全窗口)(S)', 'Stop Monitoring (All Windows)', 'モニタ停止(全ウィンドウ)(S)'),
 'options_common_setting': ('通用设置', 'Common Setting', '共通設定'),
 'series_q': ('QCPU(Q模式)', 'QCPU (Q mode)', 'QCPU(Qモード)'),
 'task_open_registration': ('打开任务登录(&T)', 'Open &Task Setting', 'タスク登録を開く(&T)'),
 'nav_initial_program': ('初始程序', 'Initial Program', '初期プログラム'),
 'nav_scan_program': ('扫描程序', 'Scan Program', 'スキャンプログラム'),
 'nav_standby_program': ('待机程序', 'Standby Program', '待機プログラム'),
 'nav_fixed_scan_program': ('恒定周期程序', 'Fixed Scan Program', '定周期プログラム'),
 'nav_no_execution_type': ('无执行类型指定', 'No Execution Type', '実行タイプ指定なし'),
 'new_kind_program': ('程序块', 'Program Block', 'プログラムブロック'),
 'new_kind_global': ('全局标签', 'Global Label', 'グローバルラベル'),
 'new_kind_structure': ('结构体', 'Structured Data Types', '構造体'),
 'new_kind_fb': ('FB', 'Function Block', 'ファンクションブロック'),
 'new_kind_function': ('函数', 'Function', 'ファンクション'),
 'allocation_overlap_format': ('%1!s!的设置与PLC参数中的锁存设置范围重复。\n是否继续处理？',
                               '%1!s! : system label range overlaps with the range of latch setting of PLC '
                               'parameter.\n'
                               'Do you want to continue?',
                               '%1!s! の設定が、PCパラメータにてラッチ設定している範囲と重なっております。\n処理を続けますか？'),
 'allocation_rebuild_notice': ('自动分配软元件设置被更改，因此需要全部编译工程。\n'
                               '确定吗？\n'
                               '\n'
                               '全部编译后，标签的软元件分配将发生更改，\n'
                               '因此软元件分配后的标签中仍保留程序更改前的软元件值。\n'
                               '安全起见，请执行可编程控制器CPU的复位、\n'
                               '包含锁存的软元件存储器的全部清除或文件寄存器的全部清除后运行。',
                               'It is necessary to execute Rebuild All for the project.\n'
                               'because the automatic-assign setting has been changed.\n'
                               'Do you want to continue?\n'
                               '\n'
                               'After executing a Rebuild All, when labels are reassigned to\n'
                               'new devices, values from the previous program will remain in\n'
                               'the previously assigned devices.\n'
                               'For safety, please RUN after executing a reset of programmable\n'
                               'controller CPU, clear all the device memory including latched\n'
                               'devices, and clear all file registers.',
                               '自動割付デバイス設定が変更されたため、プロジェクトの\n'
                               '全コンパイルが必要となります。\n'
                               'よろしいですか？\n'
                               '\n'
                               '全コンパイル後は、ラベルのデバイス割付が変更されるため、\n'
                               'デバイス割付後のラベルにはプログラム変更前のデバイス値が残ります。\n'
                               '安全のため、シーケンサCPUのリセット、ラッチを含む\n'
                               'デバイスメモリオールクリア、ファイルレジスタオールクリアを\n'
                               '実行後、RUNしてください。'),
 'nav_globals': ('全局标签', 'Global Label', 'グローバルラベル'),
 'nav_pous': ('程序部件', 'POU', 'プログラム部品'),
 'nav_programs': ('程序', 'Program', 'プログラム'),
 'nav_body': ('程序本体', 'Program', 'プログラム本体'),
 'nav_labels': ('局部标签', 'Local Label', 'ローカルラベル'),
 'nav_structures': ('结构体', 'Structured Data Types', '構造体'),
 'nav_settings': ('程序设置', 'Program Setting', 'プログラム設定'),
 'nav_execution': ('执行程序', 'Execution Program', '実行プログラム'),
 'language_fbd': ('结构化梯形图/FBD', 'Structured Ladder/FBD', '構造化ラダー/FBD'),
 'project_structured': ('结构化工程', 'Structured Project', '構造化プロジェクト'),
 'dialog_open': ('打开工程', 'Open', 'プロジェクトを開く'),
 'dialog_save_as': ('工程另存为', 'Save As', 'プロジェクトの名前を付けて保存'),
 'dialog_csv_write': ('写入至CSV文件', 'Write to CSV File', 'CSVファイルへ書込'),
 'dialog_csv_read': ('从CSV文件读取', 'Read from CSV File', 'CSVファイルからの読出'),
 'dialog_label': ('标签登录/选择', 'Label Registration/Selection', 'ラベル登録/選択'),
 'chooser_all': ('<全部>', '<ALL>', '<すべて>'),
 'title_global': ('全局标签设置', 'Global Label Setting', 'グローバルラベル設定'),
 'title_program': ('局部标签设置', 'Local Label Setting', 'ローカルラベル設定'),
 'title_fb': ('函数/FB标签设置', 'Function/FB Label Setting', 'ファンクション/FBラベル設定'),
 'title_structure': ('结构体设置', 'Structure Setting', '構造体設定'),
 'title_task': ('任务登录', 'Task Setting', 'タスク登録'),
 'delete_cursor_row': ('删除光标行。确定吗？',
                       'The cursor line will be deleted. Are you sure?',
                       'カーソル行を削除します。よろしいですか？'),
 'compile_confirm': ('是否执行全部编译？', 'Are you sure you want to Rebuild All?', '全コンパイルを実行してもよろしいですか？'),
 'auth_save_notice': ('用户认证模式设置为了启用，但未启用操作履历的参数设置。\n启用PLC参数-[PLC RAS设置]-[操作履历]。',
                      'Parameter settings for Operation History are not enabled though user authentication '
                      'mode has been enabled.\n'
                      'Enable the settings for [Operation History] (PLC Parameter -> [PLC RAS]).',
                      'ユーザ認証モードが有効の設定ですが、操作履歴のパラメータ設定が有効になっていません。\nPCパラメータ-[PC RAS設定]-[操作履歴]を有効にします。'),
 'csv_write_confirm': ('将当前显示的标签信息写入至CSV文件。\n确定吗？\n\n注意\n·未写入结构体软元件设置信息。',
                       'Currently displayed label information will be written to CSV file.\n'
                       'Are you sure you want to continue?\n'
                       '\n'
                       'Caution\n'
                       '- Structure device setting information will not be written.',
                       '表示中のラベル情報をCSVファイルへ書き込みます。\nよろしいですか？\n\n注意\n・構造体デバイス設定情報は書き込まれません。'),
 'csv_read_confirm': ('读取指定的文件内容。\n确定吗？\n\n注意\n·放弃当前的标签编辑器的设置内容。\n·当前打开的窗口过多，读取可能会花费较长时间。\n·执行读取后，将无法撤消。',
                      'Reading of the specified file will start.\n'
                      'Do you want to continue?\n'
                      '\n'
                      'Caution\n'
                      '-The current header setting will be deleted.\n'
                      '-If many windows are open, it may take more time to read.\n'
                      '-Once reading is executed, it cannot be undone.',
                      '指定されたファイルの内容を読み出します。\n'
                      'よろしいですか?\n'
                      '\n'
                      '注意\n'
                      '・現在のラベルエディタの設定内容は破棄されます。\n'
                      '・開いているウィンドウが多い場合、読み出しに時間がかかる場合があります。\n'
                      '・読み出しを行うと元に戻すことができません。'),
 'compile_completed_format': ('已完成%s。', '%s Completed.', '%sを完了しました。'),
 'compile_rebuild': ('全部编译', 'Rebuild All', '全コンパイル'),
 'cross_reference': ('交叉参照', 'Cross Reference', 'クロスリファレンス'),
 'xref_summary_format': ('%s件：软元件/标签"%s"的交叉参照信息',
                         "%s: cross reference information of device/label '%s'",
                         '%s件：デバイス/ラベル"%s"のクロスリファレンス情報'),
 'diagnostic_result': ('结果', 'Result', '結果'),
 'diagnostic_data': ('数据名', 'Data Name', 'データ名'),
 'diagnostic_class': ('分类', 'Class', '分類'),
 'diagnostic_content': ('内容', 'Content', '内容'),
 'diagnostic_code': ('错误代码', 'Error Code', 'エラーコード'),
 'diagnostic_error': ('Error', 'ERROR', 'Error'),
 'diagnostic_warning': ('Warning', 'WARNING', 'Warning'),
 'diagnostic_checkwarning': ('CheckWarning', 'CheckWarning', 'CheckWarning'),
 'diagnostic_information': ('Information', 'Information', 'Information'),
 'xref_rw': ('读入/写入', 'Read/Write', '読込/書込'),
 'xref_data': ('数据名', 'Data Name', 'データ名'),
 'xref_label': ('软元件/标签', 'Device/Label', 'デバイス/ラベル'),
 'xref_position': ('位置', 'Position', '位置'),
 'xref_program': ('程序文件名', 'Program File Name', 'プログラムファイル名'),
 'ok': ('确定', 'OK', 'OK'),
 'cancel': ('取消', 'Cancel', 'キャンセル'),
 'ladder_block_no': ('梯形图块号', 'Ladder Block No.', '回路ブロックNo.'),
 'graph_overlap': ('梯形图图形重叠。是否继续处理？',
                   'Ladder diagrams are overlapped. Are you sure to continue processing?',
                   '回路図形が重なります。処理を続けますか？'),
 'column_class': ('类', 'Class', 'クラス'),
 'column_name': ('标签名', 'Label Name', 'ラベル名'),
 'column_type': ('数据类型', 'Data Type', 'データ型'),
 'column_constant': ('常量', 'Constant', '定数値'),
 'column_comment': ('注释', 'Comment', 'コメント'),
 'column_device': ('软元件', 'Device', 'デバイス'),
 'column_address': ('地址', 'Address', 'アドレス'),
 'column_remark': ('备注', 'Remark', '備考'),
 'column_relation': ('系统标签的关联', 'Relation with System Label', 'システムラベルの関連'),
 'column_system_name': ('系统标签名', 'System Label Name', 'システムラベル名'),
 'column_attribute': ('属性', 'Attribute', '属性'),
 'allocation_grid_parameter': ('PLC参数', 'PLC Parameter', 'PCパラメータ'),
 'allocation_grid_parameter_range': ('软元件设置范围', 'Device Setting Range', 'デバイス設定範囲'),
 'allocation_grid_separator': ('～', '--', '～'),
 'allocation_grid_assign': ('分配', 'Assign', '割付'),
 'allocation_grid_range': ('分配范围', 'Assignment Range', '割付範囲'),
 'allocation_grid_total': ('合计点数', 'Total Points', '合計点数'),
 'allocation_grid_device': ('软元件', 'Device', 'デバイス'),
 'allocation_grid_digit': ('进制', 'Digit', '進'),
 'allocation_grid_selection': ('选择', 'Selection', '選択'),
 'allocation_grid_start': ('起始', 'Start', '先頭'),
 'allocation_grid_end': ('结束', 'End', '最終'),
 'allocation_grid_word': ('字软元件', 'Word Device', 'ワードデバイス'),
 'allocation_grid_bit': ('位软元件', 'Bit Device', 'ビットデバイス'),
 'allocation_grid_pointer': ('指针', 'Pointer', 'ポインタ'),
 'allocation_grid_timer': ('定时器', 'Timer', 'タイマ'),
 'allocation_grid_retentive': ('累积定时器', 'Retentive Timer', '積算タイマ'),
 'allocation_grid_counter': ('计数器', 'Counter', 'カウンタ'),
 'allocation_grid_var': ('VAR用', 'VAR Range', 'VAR用'),
 'allocation_grid_var_retain': ('VAR_RETAIN用', 'VAR_RETAIN Range', 'VAR_RETAIN用'),
 'allocation_grid_latch1': ('锁存(1)', 'Latch(1)', 'ラッチ(1)'),
 'allocation_grid_latch2': ('锁存(2)', 'Latch(2)', 'ラッチ(2)'),
 'allocation_grid_d_latch': ('D锁存', 'D Latch', 'Dラッチ'),
 'allocation_grid_w_latch': ('W锁存', 'W Latch', 'Wラッチ'),
 'allocation_grid_zr_latch': ('ZR锁存', 'ZR Latch', 'ZRラッチ'),
 'allocation_grid_l_latch': ('L锁存', 'L Latch', 'Lラッチ'),
 'allocation_grid_b_latch': ('B锁存', 'B Latch', 'Bラッチ'),
 'allocation_grid_t_latch': ('T锁存', 'T Latch', 'Tラッチ'),
 'allocation_grid_st_latch': ('ST锁存', 'ST Latch', 'STラッチ'),
 'allocation_grid_c_latch': ('C锁存', 'C Latch', 'Cラッチ'),
 'dialog_options': ('选项', 'Options', 'オプション'),
 'dialog_new': ('新建', 'New Project', '新規作成'),
 'dialog_new_data': ('新建数据', 'New Data', 'データ新規作成'),
 'dialog_new_data_navigation': ('新建数据', 'Add New Data', 'データ新規作成'),
 'dialog_allocation': ('自动分配软元件设置', 'Device/Label Automatic-Assign Setting', '自動割付デバイス設定'),
 'dialog_instance': ('FB实例选择', 'Change Instance(Function Block)', 'FBインスタンス選択'),
 'application': ('MELSOFT系列 GX Works2', 'MELSOFT Series GX Works2', 'MELSOFTシリーズ GX Works2'),
 'dialog_instruction': ('指令输入', 'Input Instruction', '命令入力'),
 'dialog_jump': ('跳转', 'Jump', 'ジャンプ'),
 'options_gb18030': ('将简体中文(GB18030)字符集设置为可使用(&G)',
                     'Enable &GB18030 character set in Chinese (Simplified)',
                     '中国語(簡体字)でGB18030文字セットを使用可能にする(&G)'),
 'instance_clear': ('解除选择(&C)', '&Clear', '選択解除(&C)'),
 'yes': ('是(&Y)', '&Yes', 'はい(&Y)'),
 'no': ('否(&N)', '&No', 'いいえ(&N)'),
 'menu_find': ('搜索/替换(&F)', '&Find/Replace', '検索/置換(&F)'),
 'menu_cross_reference': ('交叉参照(&S)', 'Cro&ss Reference', 'クロスリファレンス(&S)'),
 'project_codepage_status_japanese': ('日文', 'Japanese', '日本語'),
 'project_codepage_status_english': ('英语', 'English', '英語'),
 'project_codepage_status_simplified_chinese': ('简体中文', 'Chinese (Simplified)', '中国語（簡体字）'),
 'project_codepage_status_korean': ('韩文', 'Korean', '韓国語'),
 'project_codepage_status_traditional_chinese': ('繁体中文', 'Chinese (Traditional)', '中国語（繁体字）')}
