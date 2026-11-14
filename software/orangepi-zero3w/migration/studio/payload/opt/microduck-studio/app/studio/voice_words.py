"""Shared command vocabulary for speech decoding and server-side parsing."""

WORDS = {
    '鸭哥': 'wake', '走': 'forward', '前进': 'forward', '向前走': 'forward', '往前走': 'forward',
    '后退': 'backward', '退后': 'backward', '往后走': 'backward', '向后走': 'backward',
    '左转': 'left', '向左转': 'left', '右转': 'right', '向右转': 'right',
    '坐下': 'sit', '站起来': 'stand', '站起': 'stand', '开始走': 'walk', '起坐': 'toggle', '坐起': 'toggle',
    '翻跟头': 'roll', '翻个跟头': 'roll', '前滚翻': 'roll',
    '停': 'stop', '停下': 'stop', '停止': 'stop', '别走了': 'stop',
    '往前': 'forward', '向前': 'forward', '往后': 'backward', '向后': 'backward',
    '走一步': 'forward', '走一点': 'forward', '走一下': 'forward', '走两步': 'forward',
    '往左转': 'left', '往右转': 'right', '不要走了': 'stop',
}
WAKE_FORMS = ('鸭哥', '雅阁', '雅各', '亚哥', '压哥')
NEGATIONS = ('不要', '不能', '不许', '别', '不想', '不用', '不准',
             '不前进', '不后退', '不左转', '不右转', '不坐', '不站', '不翻', '不走', '不转', '不往', '不向')
FILLERS = ('请', '你', '我', '想', '让', '帮我', '现在', '先', '然后', '再', '吧', '嗯',
           '一下', '一点', '一小步', '两步', '看看', '听见了吗', '不', '没', '没有')
RECOGNITION_PHRASES = tuple(dict.fromkeys((*WORDS, *WAKE_FORMS, *NEGATIONS, *FILLERS)))
