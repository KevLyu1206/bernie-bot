# -*- coding: utf-8 -*-
"""食堂菜单 → 中文 + 贝尔的推荐语（给 menu5c 用）

三件事：
    tr(name)                     "roasted eye of round + peppercorn demi-glace"
                                 → "烤牛后腿肉＋黑胡椒酱"
    pick_recommend(hall, meals)  按日期 + 食堂固定的一条女仆口吻推荐
    score_of(hall, meals)        0-100 的「今天这家值不值得去」

翻译是本地词表拼出来的（先多词短语、再单词），不联网、不花 token；
词表里没有的词就原样留着（品牌名本来就该留：Beyond / Impossible / Oreo）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
import re
from typing import Any

# ---------------------------------------------------------------- 多词短语（优先匹配、越长越先）

PHRASES: dict[str, str] = {
    # 早餐
    "cage free scrambled eggs": "散养鸡蛋炒蛋",
    "cage-free scrambled eggs": "散养鸡蛋炒蛋",
    "scrambled vegan egg substitute": "素炒蛋",
    "scrambled egg whites": "蛋白炒蛋",
    "cage free egg whites": "散养蛋白",
    "cage free fried eggs": "散养煎蛋",
    "cage free scrambled eggs and ham": "火腿炒蛋",
    "just eggs scrambled and broccolili": "西兰花素炒蛋",
    "just eggs scrambled and broccoli": "西兰花素炒蛋",
    "hard boiled egg": "水煮蛋",
    "hard-boiled egg": "水煮蛋",
    "fried egg": "煎蛋",
    "scrambled eggs": "炒蛋",
    "scrambled whole eggs": "全蛋炒蛋",
    "made to order filled omelet": "现做煎蛋卷",
    "build your own omelet burrito": "自选蛋卷饼",
    "omelet bar": "煎蛋卷吧",
    "buttermilk pancakes": "酪乳松饼",
    "chocolate chip pancakes": "巧克力豆松饼",
    "blueberry pancakes": "蓝莓松饼",
    "banana pancakes": "香蕉松饼",
    "two buttermilk pancakes": "两块酪乳松饼",
    "tres leches french toast": "三奶法式吐司",
    "french toast": "法式吐司",
    "belgian waffle": "比利时华夫饼",
    "biscuits and gravy": "比司吉配肉汁",
    "sausage gravy": "香肠肉汁",
    "hash brown patty": "薯饼",
    "hash browns": "薯饼",
    "hash brown": "薯饼",
    "fried tater tots": "炸薯球",
    "tater tots": "薯球",
    "sweet potato tots": "红薯球",
    "baked sweet potato tots": "烤红薯球",
    "breakfast potatoes": "早餐土豆",
    "roasted potato wedges": "烤土豆角",
    "potato wedges": "土豆角",
    "breakfast sausage patty": "早餐香肠饼",
    "breakfast sausage patties": "早餐香肠饼",
    "pork breakfast sausage patties": "猪肉早餐香肠饼",
    "pork sausage link": "猪肉香肠",
    "turkey sausage links": "火鸡香肠",
    "turkey sausage link": "火鸡香肠",
    "turkey sausage patty": "火鸡香肠饼",
    "chicken apple sausage": "鸡肉苹果香肠",
    "chicken apple sausage link": "鸡肉苹果香肠",
    "applewood bacon": "苹果木烟熏培根",
    "apple smoked bacon": "苹果木烟熏培根",
    "turkey bacon": "火鸡培根",
    "canadian bacon": "加拿大培根",
    "breakfast pork chorizo burritos": "早餐辣香肠卷饼",
    "hot cereal": "热麦片",
    "steel cut oatmeal": "钢切燕麦粥",
    "oat milk overnight oats": "燕麦奶隔夜麦片",
    "overnight oats": "隔夜麦片",
    "old fashioned oatmeal": "传统燕麦粥",
    "old-fashioned oatmeal": "传统燕麦粥",
    "coconut chia pudding": "椰香奇亚籽布丁",
    "breakfast @ home": "家常早餐",
    "breakfast options": "",
    # 主食 / 碳水
    "macaroni and cheese": "芝士通心粉",
    "mac and cheese": "芝士通心粉",
    "grilled cheese": "烤芝士三明治",
    "peanut butter and jelly": "花生酱果酱三明治",
    "chicken and rice soup": "鸡肉米饭汤",
    "jasmine rice": "茉莉香米饭",
    "basmati rice": "印度香米饭",
    "brown basmati rice": "印度香糙米饭",
    "brown rice": "糙米饭",
    "fried rice": "炒饭",
    "steamed rice": "白米饭",
    "white rice": "白米饭",
    "rice pilaf": "香料抓饭",
    "brown rice pilaf": "糙米抓饭",
    "rice noodles": "米粉",
    "rice flour focaccia bread": "米粉佛卡夏",
    "garlic bread": "蒜香面包",
    "dan dan": "担担", "pad thai": "泰式炒河粉", "hash brown o'brien": "洋葱彩椒薯饼",
    "overnight oats": "隔夜麦片", "spring roll": "春卷", "spring rolls": "春卷",
    "baba ganoush": "茄子泥", "chana masala": "鹰嘴豆咖喱", "denver omelet": "丹佛煎蛋卷",
    "fl oz": "液量盎司", "garbanzo beans": "鹰嘴豆",
    "carve craft": "现切烤肉", "the carvecraft": "现切烤肉", "pozole verde": "绿玉米汤",
    "pozoleverde": "绿玉米汤", "halal herb chicken": "清真香草鸡",
    "chicken pozole verde": "鸡肉绿玉米汤", "chicken pozoleverde": "鸡肉绿玉米汤",
    "brussels sprouts": "抱子甘蓝", "butternut squash": "奶油南瓜",
    "pho noodle bowl": "越南河粉碗", "quick vegetable pho broth": "蔬菜河粉清汤",
    "mongolian stir fry": "蒙古风味炒", "mongolian beef": "蒙古牛肉",
    "whole eats station": "全食档口", "italian pitzer sunday": "意式 Pitzer 周末面",
    "szechuan peppercorns": "花椒", "pickled cucumber": "腌黄瓜",
    "chili beans": "辣豆", "garbanzo chili beans": "鹰嘴豆辣豆",
    "chipotle garbanzo chili beans": "烟熏辣椒鹰嘴豆辣豆",
    "chilaquiles verdes": "墨西哥玉米片炒蛋", "chilaquilesverdes": "墨西哥玉米片炒蛋",
    "lentil beans": "扁豆", "yu choy": "菜心", "yuchoy": "菜心", "aromatic rice": "香米饭",
    "sweet potatoes": "红薯", "chili dog": "辣酱热狗", "carvecraft": "现切烤肉台",
    "pepper steak": "黑椒牛排", "steak stir fry": "黑椒牛排炒", "stir fry": "炒",
    "mocha whipped cream": "摩卡奶油", "teriyaki chicken": "照烧鸡",
    "general tso chicken": "左宗棠鸡", "orange chicken": "陈皮鸡",
    "kung pao chicken": "宫保鸡丁", "sweet and sour chicken": "糖醋鸡",
    "beef and broccoli": "西兰花炒牛肉", "chicken tikka masala": "印度咖喱鸡",
    "focaccia bread": "佛卡夏面包",
    "cornbread": "玉米面包",
    "flour tortilla": "面粉薄饼",
    "corn tortilla": "玉米薄饼",
    "flatbread": "烤薄饼",
    "pita bread": "皮塔饼",
    "deluxe bagel bar": "豪华贝果吧",
    "lox and bagel bar": "烟熏三文鱼贝果吧",
    "bagel bar": "贝果吧",
    "avocado toast": "牛油果吐司",
    "mashed potatoes": "土豆泥",
    "garlic mashed potatoes": "蒜香土豆泥",
    "garlic mashed potatoes + boursin cheese": "蒜香土豆泥＋布辛芝士",
    "roasted red potatoes": "烤红皮土豆",
    "rosemary and garlic roasted red potatoes": "迷迭香蒜烤红皮土豆",
    "crispy smashed red bliss potato": "脆压红皮土豆",
    "roasted fingerling potatoes": "烤手指土豆",
    "roasted potato wedges": "烤土豆角",
    "scalloped potatoes": "奶油焗土豆片",
    "cheese ravioli": "芝士意式方饺",
    "ravioli": "意式方饺",
    "tortellini": "意式水饺",
    "spaghetti pasta": "意大利面",
    "fettuccini pasta": "宽意大利面",
    "penne pasta": "通心粉",
    "gluten free pasta": "无麸质意面",
    "macaroni salad": "通心粉沙拉",
    "singapore noodles": "新加坡炒米粉",
    "chow mein": "炒面",
    "lo mein": "捞面",
    # 肉 / 海鲜
    "chicken breast": "鸡胸肉",
    "chicken thigh": "鸡腿肉",
    "grilled halal chicken": "烤清真鸡肉",
    "halal herb chicken": "香草清真鸡肉",
    "korean grilled chicken": "韩式烤鸡",
    "gochujang chicken": "韩式辣酱鸡",
    "sesame ginger chicken": "芝麻姜味鸡",
    "teriyaki pork ribs": "照烧猪肋排",
    "fried chicken tenders": "炸鸡柳",
    "chicken tenders": "鸡柳",
    "chicken nuggets": "鸡块",
    "grilled italian sausage": "意式烤香肠",
    "italian sausage": "意式香肠",
    "buffalo chicken wings": "水牛城鸡翅",
    "chicken wings": "鸡翅",
    "fried chicken": "炸鸡",
    "pulled pork": "手撕猪肉",
    "pork carnitas": "慢炖猪肉",
    "beef barbacoa": "慢炖牛肉",
    "eye of round": "牛后腿肉",
    "roasted eye of round": "烤牛后腿肉",
    "sliced roast beef": "烤牛肉片",
    "beef meatballs": "牛肉丸",
    "turkey meat balls": "火鸡肉丸",
    "turkey meatballs": "火鸡肉丸",
    "impossible meatballs": "Impossible 素肉丸",
    "smashed beef kebab": "手打牛肉串",
    "beef kebab": "牛肉串",
    "korean bbq beef bulgogi": "韩式烤牛肉",
    "beef bulgogi": "韩式烤牛肉",
    "baked bbq salmon": "烤烧烤三文鱼",
    "grilled salmon": "烤三文鱼",
    "lemon parsley rockfish": "柠檬香芹石斑鱼",
    "smoked salmon": "烟熏三文鱼",
    "fish tacos": "鱼肉塔可",
    "fish and chips": "炸鱼薯条",
    # 素食 / 植物基
    "beyond burger patty": "Beyond 素肉饼",
    "beyond sausage patty": "Beyond 素香肠饼",
    "impossible breakfast patties": "Impossible 素早餐饼",
    "impossible meat corn beef style": "Impossible 素咸牛肉",
    "impossible plant-based chili": "Impossible 素辣豆酱",
    "veggie chick'n nuggets": "素鸡块",
    "plant based fried chicken": "植物基炸鸡",
    "plant-based fried chicken": "植物基炸鸡",
    "incogmeato chicken tenders": "Incogmeato 素鸡柳",
    "vegan hot dog": "纯素热狗",
    "sweet and sour plant based chicken": "糖醋植物基鸡",
    "morningstar sausage patty": "Morningstar 素香肠饼",
    "morningstar vegetarian sausage patties": "Morningstar 素香肠饼",
    "scrambled tofu": "炒豆腐",
    "vegan tofu spinach scramble": "纯素豆腐菠菜炒",
    "vegan banana cream pie": "纯素香蕉奶油派",
    # 三明治 / 汉堡 / 披萨
    "mushroom goat cheese burger": "蘑菇山羊芝士汉堡",
    "barbecue bacon cheeseburger": "烧烤培根芝士汉堡",
    "buffalo chicken meltdown sandwich": "水牛城鸡肉热熔三明治",
    "spicy crispy chicken sandwich": "香辣脆鸡堡",
    "grilled chicken sandwich": "烤鸡三明治",
    "tuna salad sandwich": "金枪鱼三明治",
    "cheese pizza": "芝士披萨",
    "pepperoni pizza": "意式香肠披萨",
    "margherita pizza": "玛格丽特披萨",
    "veggie pizza": "蔬菜披萨",
    "cheese quesadilla": "芝士饼",
    "chicken quesadilla": "鸡肉芝士饼",
    # 汤 / 炖
    "chicken pozole soup": "鸡肉玉米浓汤",
    "cream of tomato soup": "奶油番茄汤",
    "tomato soup": "番茄汤",
    "chicken noodle soup": "鸡汤面",
    "beef and corn chowder soup": "牛肉玉米浓汤",
    "bacon and corn chowder soup": "培根玉米浓汤",
    "corn chowder": "玉米浓汤",
    "clam chowder": "蛤蜊浓汤",
    "minestrone": "意式蔬菜汤",
    "beef menudo": "墨西哥牛肚汤",
    "spanish style lentil potato and tempeh": "西班牙扁豆土豆天贝",
    "chicken tortilla soup": "鸡肉玉米饼汤",
    # 甜品 / 水果
    "chocolate chip cookies": "巧克力豆曲奇",
    "chocolate chip cookie": "巧克力豆曲奇",
    "chocolate chip croissants": "巧克力豆可颂",
    "chocolate raspberry scones": "巧克力树莓司康",
    "coconut chocolate orange scones": "椰香巧克力橙皮司康",
    "glazed donut holes": "糖霜甜甜圈球",
    "powder sugar donuts": "糖粉甜甜圈",
    "banana foster cinnamon rolls": "香蕉肉桂卷",
    "apple spice cheesecake cup": "苹果香料芝士蛋糕杯",
    "cherry cheesecake cup": "樱桃芝士蛋糕杯",
    "apple and pear cherry pie": "苹果梨樱桃派",
    "oreo m&m brookie": "奥利奥 M&M 布朗尼曲奇",
    "assorted mini scones": "什锦迷你司康",
    "classic blueberry muffin": "经典蓝莓玛芬",
    "blueberry muffins": "蓝莓玛芬",
    "cranberry orange muffin": "蔓越莓橙皮玛芬",
    "glazed cinnamon roll": "糖霜肉桂卷",
    "cinnamon roll": "肉桂卷",
    "cinnamon rolls": "肉桂卷",
    "assorted breakfast pastries": "什锦早餐酥点",
    "french apple cake bar": "法式苹果蛋糕条",
    "strawberry gelatin": "草莓果冻",
    "orange gelatin": "橙子果冻",
    "berry blue gelatin": "蓝莓果冻",
    "chocolate pudding": "巧克力布丁",
    "vanilla pudding": "香草布丁",
    "acai blackberry paradise bowl": "巴西莓黑莓碗",
    "acai berry sorbet": "巴西莓雪葩",
    "acai bowls": "巴西莓碗",
    "strawberry bar": "草莓酥条",
    "mixed berry bar": "什锦莓果酥条",
    "chocolate cake": "巧克力蛋糕",
    "carrot cake": "胡萝卜蛋糕",
    # 沙拉
    "asian cucumber and carrot salad": "亚洲黄瓜胡萝卜沙拉",
    "chopped romaine lettuce": "罗马生菜碎",
    "pitzer caesar salad": "Pitzer 凯撒沙拉",
    "caesar salad": "凯撒沙拉",
    "cobb salad": "科布沙拉",
    "greek salad": "希腊沙拉",
    "potato salad": "土豆沙拉",
    "blueberry, quinoa, and kale salad": "蓝莓藜麦羽衣甘蓝沙拉",
    "southwestern salad": "西南风味沙拉",
    # 其它常见
    "hummus": "鹰嘴豆泥",
    "house-made sesame hummus": "自制芝麻鹰嘴豆泥",
    "pico de gallo": "番茄洋葱莎莎",
    "salsa roja": "红莎莎酱",
    "salsa verde": "绿莎莎酱",
    "homemade salsa roja": "自制红莎莎酱",
    "homemade salsa verde": "自制绿莎莎酱",
    "chipotle mayo": "烟熏辣椒蛋黄酱",
    "korean bbq sauce": "韩式烧烤酱",
    "basil marinara sauce": "罗勒番茄酱",
    "marinara sauce": "番茄酱",
    "pesto sauce": "青酱",
    "chipotle cream sauce": "烟熏辣椒奶油酱",
    "beef gravy": "牛肉汁",
    "mashed potato bar": "土豆泥吧",
    "baked potato bar": "烤土豆吧",
    "smoothie bar": "冰沙吧",
    "salad bar": "沙拉吧",
    "deli bar": "熟食吧",
    "nacho bar": "玉米片吧",
    "toppings bar": "配料台",
    "wing night": "鸡翅之夜",
    "wing bar": "鸡翅吧",
    "sandwich station": "三明治台",
    "stir fry station": "炒菜台",
    "grill market": "烧烤档",
    "pasta bar": "意面吧",
    "pitzer pasta bar": "Pitzer 意面吧",
    # —— 第二轮补充 ——
    "green beans": "四季豆", "snap peas": "甜豆", "snow peas": "荷兰豆",
    "soft tacos": "软塔可", "hard shell tacos": "脆壳塔可", "fajita vegetables": "铁板蔬菜",
    "hot fudge": "热巧克力酱", "german chocolate cake": "德式巧克力蛋糕",
    "coconut icing": "椰子糖霜", "cream cheese": "奶油芝士", "whipped topping": "打发奶油",
    "angel food cake": "天使蛋糕", "long grain rice": "长粒米饭", "yukon potatoes": "育空土豆",
    "red velvet": "红丝绒", "fruity pebbles": "Fruity Pebbles", "bow tie pasta": "蝴蝶意面",
    "pulled pork": "手撕猪肉", "flank steak": "侧腹牛排", "poke bowl": "夏威夷生鱼饭",
    "tofu scramble": "豆腐炒蛋", "tofu spread": "豆腐抹酱", "meat sauce": "肉酱",
    "herb roasted turkey breast": "香草烤火鸡胸", "roasted turkey breast": "烤火鸡胸",
    "macaroni and cheese bar": "芝士通心粉吧", "nacho cheese": "玉米片芝士酱",
    "steamed vegetables": "蒸蔬菜", "mixed vegetables": "什锦蔬菜", "stir fry vegetables": "炒蔬菜",
    "caribbean bbq": "加勒比烧烤", "simply oasis": "Simply Oasis", "special hot salad": "热沙拉",
    "grilled lemon chicken": "柠檬烤鸡", "chicken parmesan": "帕玛森鸡排",
    "chicken pot pie": "鸡肉锅派", "shepherds pie": "牧羊人派", "cottage pie": "牧羊人派",
    "bean burrito": "豆子卷饼", "breakfast burrito": "早餐卷饼", "breakfast sandwich": "早餐三明治",
    "omelet station": "煎蛋卷台", "waffle bar": "华夫饼吧", "pancake bar": "松饼吧",
    "cinnamon roll": "肉桂卷", "cinnamon rolls": "肉桂卷",
    "cage free": "散养", "cage-free": "散养", "hard cooked": "全熟", "hard boiled": "水煮",
    "pot pie": "锅派", "key lime": "青柠", "fruit cup": "水果杯", "fruit salad": "水果沙拉",
    "sweet and sour": "糖醋", "general tso's": "左宗棠", "kung pao": "宫保",
    "dulce de leche": "焦糖牛奶酱", "carne asada": "烤牛肉", "pepper jack": "杰克胡椒芝士",
    "balsamic vinegar": "黑醋", "lemongrass": "香茅", "loaded fries": "豪华薯条",
    "bao bun": "刈包", "dandan noodles": "担担面", "poke bowl": "夏威夷生鱼饭",
    "chopped salad": "碎沙拉", "veggie dip": "蔬菜蘸酱", "spinach dip": "菠菜蘸酱",
    "hummus dip": "鹰嘴豆泥蘸酱", "garlic bread": "蒜香面包",
}

# ---------------------------------------------------------------- 单词 / 短词

WORDS: dict[str, str] = {
    # 做法
    "grilled": "烤", "roasted": "烤", "baked": "焗烤", "braised": "炖", "steamed": "蒸",
    "fried": "炸", "deep-fried": "炸", "pan-fried": "煎", "sauteed": "炒", "sautéed": "炒",
    "saute": "炒", "sauté": "炒", "stir": "爆炒", "smoked": "烟熏", "charred": "炭烤",
    "glazed": "蜜汁", "caramelized": "焦糖", "crispy": "脆", "creamy": "奶油",
    "spicy": "香辣", "seasoned": "调味", "marinated": "腌", "battered": "挂糊",
    "breaded": "裹粉", "stuffed": "酿", "blanched": "焯", "scrambled": "炒",
    "poached": "水波", "grill": "烧烤", "oven": "烤箱", "baked ": "烤",
    # 形容词
    "fresh": "新鲜", "freshly": "现做", "house-made": "自制", "homemade": "自制",
    "assorted": "什锦", "mixed": "什锦", "classic": "经典", "deluxe": "豪华",
    "mini": "迷你", "individual": "单人份", "delicious": "美味", "vegan": "纯素",
    "vegetarian": "素", "plant-based": "植物基", "plant": "植物基", "based": "",
    "gluten": "麸质", "free": "免", "low": "低", "nonfat": "脱脂", "whole": "全麦",
    "old": "老式", "new": "新", "quick": "快手", "scratch": "现做", "real": "真",
    "mini ": "迷你", "extra": "加料", "double": "双份", "baby": "小",
    # 肉 / 蛋白
    "chicken": "鸡肉", "beef": "牛肉", "pork": "猪肉", "turkey": "火鸡肉", "lamb": "羊肉",
    "ham": "火腿", "bacon": "培根", "sausage": "香肠", "patty": "肉饼", "patties": "肉饼",
    "steak": "牛排", "sirloin": "西冷牛排", "meatballs": "肉丸", "meatball": "肉丸",
    "meat": "肉", "breast": "胸肉", "thigh": "腿肉", "wings": "鸡翅", "wing": "鸡翅",
    "tenders": "柳", "nuggets": "鸡块", "egg": "蛋", "eggs": "鸡蛋", "whites": "蛋白",
    "tofu": "豆腐", "tempeh": "天贝", "seitan": "面筋", "chickpea": "鹰嘴豆",
    "chickpeas": "鹰嘴豆", "lentil": "扁豆", "lentils": "扁豆", "beans": "豆",
    "bean": "豆", "salmon": "三文鱼", "tuna": "金枪鱼", "fish": "鱼", "shrimp": "虾",
    "clam": "蛤蜊", "crab": "蟹", "rockfish": "石斑鱼", "cod": "鳕鱼",
    "bacon bits": "培根碎", "chorizo": "辣香肠", "pepperoni": "意式香肠",
    "ham steak": "火腿排", "roast": "烤", "ribs": "肋排", "bulgogi": "韩式烤牛肉",
    # 奶 / 芝士
    "cheese": "芝士", "cheddar": "切达芝士", "parmesan": "帕玛森芝士",
    "mozzarella": "马苏里拉", "feta": "菲达芝士", "boursin": "布辛芝士",
    "cream": "奶油", "milk": "牛奶", "butter": "黄油", "yogurt": "酸奶",
    "eggnog": "蛋奶酒", "buttermilk": "酪乳",
    # 蔬菜 / 菌菇
    "potato": "土豆", "potatoes": "土豆", "sweet": "甜", "carrot": "胡萝卜",
    "carrots": "胡萝卜", "broccoli": "西兰花", "broccolili": "西兰花",
    "cauliflower": "花椰菜", "spinach": "菠菜", "kale": "羽衣甘蓝", "lettuce": "生菜",
    "romaine": "罗马生菜", "arugula": "芝麻菜", "cabbage": "卷心菜", "slaw": "卷心菜丝",
    "tomato": "番茄", "tomatoes": "番茄", "cucumber": "黄瓜", "zucchini": "西葫芦",
    "eggplant": "茄子", "mushroom": "蘑菇", "mushrooms": "蘑菇", "portobello": "波特贝勒菇",
    "onion": "洋葱", "onions": "洋葱", "scallions": "葱花", "leek": "韭葱",
    "corn": "玉米", "peas": "豌豆", "pea": "豌豆", "squash": "南瓜", "pumpkin": "南瓜",
    "butternut": "奶油南瓜", "avocado": "牛油果", "plantains": "大蕉", "jicama": "凉薯",
    "asparagus": "芦笋", "celery": "芹菜", "beets": "甜菜", "radish": "萝卜",
    "broccolini": "嫩茎西兰花", "brussels": "抱子甘蓝", "sprouts": "抱子甘蓝",
    "peppers": "彩椒", "pepper": "胡椒", "jalapeno": "墨西哥辣椒", "jalapeños": "墨西哥辣椒",
    "banana": "香蕉", "bananas": "香蕉", "apple": "苹果", "apples": "苹果",
    "orange": "橙子", "oranges": "橙子", "lemon": "柠檬", "lime": "青柠",
    "mango": "芒果", "pineapple": "菠萝", "watermelon": "西瓜", "grapes": "葡萄",
    "grape": "葡萄", "raisin": "葡萄干", "raisins": "葡萄干", "cranberry": "蔓越莓",
    "cranberries": "蔓越莓", "blueberry": "蓝莓", "blueberries": "蓝莓",
    "strawberry": "草莓", "strawberries": "草莓", "raspberry": "树莓",
    "raspberries": "树莓", "berries": "莓果", "berry": "莓果", "coconut": "椰子",
    "citrus": "柑橘", "pear": "梨", "peach": "桃子", "cherry": "樱桃",
    "pumpkin seeds": "南瓜籽", "craisins": "蔓越莓干",
    # 调味 / 酱
    "garlic": "蒜香", "herb": "香草", "herbs": "香草", "basil": "罗勒", "dill": "莳萝",
    "cilantro": "香菜", "ginger": "姜", "sesame": "芝麻", "honey": "蜂蜜",
    "maple": "枫糖", "syrup": "糖浆", "sugar": "糖", "salt": "盐", "vinegar": "醋",
    "oil": "油", "olive": "橄榄", "salsa": "莎莎酱", "guacamole": "牛油果酱",
    "hummus": "鹰嘴豆泥", "mayo": "蛋黄酱", "mayonnaise": "蛋黄酱", "ranch": "牧场酱",
    "sriracha": "是拉差辣酱", "ketchup": "番茄酱", "mustard": "芥末", "bbq": "烧烤",
    "buffalo": "水牛城", "teriyaki": "照烧", "gochujang": "韩式辣酱", "curry": "咖喱",
    "chili": "辣豆酱", "chile": "辣椒", "chipotle": "烟熏辣椒", "cajun": "卡真",
    "jerk": "牙买加香料", "jamaican": "牙买加", "moroccan": "摩洛哥", "greek": "希腊",
    "italian": "意式", "french": "法式", "mexican": "墨西哥", "korean": "韩式",
    "asian": "亚洲", "cuban": "古巴", "mediterranean": "地中海", "western": "西式",
    "baja": "下加州", "hawaiian": "夏威夷", "indian": "印度", "thai": "泰式",
    "agave": "龙舌兰糖浆", "sriracha mayo": "是拉差蛋黄酱", "dressing": "沙拉酱",
    "vinaigrette": "油醋汁", "caesar": "凯撒", "pesto": "青酱", "marinara": "番茄酱",
    "alfredo": "奶油白酱", "gravy": "肉汁", "demi-glace": "半釉汁", "glaze": "釉汁",
    "soy": "酱油", "miso": "味噌", "tahini": "芝麻酱", "tzatziki": "酸奶黄瓜酱",
    "boursin cheese": "布辛芝士", "pico": "莎莎", "gallo": "莎莎",
    # 主食 / 谷物
    "rice": "米饭", "pasta": "意面", "noodle": "面条", "noodles": "面条", "pho": "越南河粉",
    "ramen": "拉面", "quinoa": "藜麦", "oatmeal": "燕麦粥", "oats": "燕麦",
    "granola": "麦片", "cereal": "麦片", "toast": "吐司", "bread": "面包",
    "roll": "小面包", "rolls": "小面包", "bun": "面包", "buns": "面包",
    "tortilla": "薄饼", "tortillas": "薄饼", "wrap": "卷饼", "bowl": "碗",
    "pilaf": "抓饭", "couscous": "库斯库斯", "barley": "大麦", "orzo": "米粒面",
    "breadstick": "面包棒", "breadsticks": "面包棒", "croutons": "面包丁",
    # 甜品 / 烘焙
    "cake": "蛋糕", "cheesecake": "芝士蛋糕", "brownie": "布朗尼", "brownies": "布朗尼",
    "blondie": "白布朗尼", "blondies": "白布朗尼", "cookie": "曲奇", "cookies": "曲奇",
    "muffin": "玛芬", "muffins": "玛芬", "scone": "司康", "scones": "司康",
    "croissant": "可颂", "croissants": "可颂", "danish": "丹麦酥", "donut": "甜甜圈",
    "donuts": "甜甜圈", "donut holes": "甜甜圈球", "cupcake": "纸杯蛋糕",
    "cupcakes": "纸杯蛋糕", "pie": "派", "pudding": "布丁", "gelatin": "果冻",
    "tiramisu": "提拉米苏", "crepe": "可丽饼", "waffle": "华夫饼", "waffles": "华夫饼",
    "pancake": "松饼", "pancakes": "松饼", "pastry": "酥点", "pastries": "酥点",
    "dessert": "甜点", "desserts": "甜点", "sorbet": "雪葩", "ice": "冰",
    "chocolate": "巧克力", "vanilla": "香草", "caramel": "焦糖", "buttercream": "奶油霜",
    "frosting": "糖霜", "sprinkles": "彩糖", "oreo": "奥利奥", "m&m": "M&M",
    "cup": "杯", "squares": "方块酥", "square": "方块酥", "stick": "条",
    # 菜型
    "pizza": "披萨", "burger": "汉堡", "hamburger": "汉堡", "cheeseburger": "芝士汉堡",
    "sandwich": "三明治", "melt": "热熔三明治", "hot dog": "热狗", "hotdogs": "热狗",
    "burrito": "卷饼", "taco": "塔可", "tacos": "塔可", "quesadilla": "芝士饼",
    "fajitas": "铁板烧", "fajita": "铁板烧", "soup": "汤", "chowder": "浓汤",
    "broth": "清汤", "bisque": "浓汤", "stew": "炖菜", "salad": "沙拉", "sliders": "小汉堡",
    "dumplings": "饺子", "potstickers": "锅贴", "gyoza": "煎饺", "eggroll": "春卷",
    "eggrolls": "春卷", "wontons": "馄饨", "nachos": "玉米片", "chips": "薯片",
    "fries": "薯条", "fry": "炒", "tots": "薯球",
    # 量词 / 其它
    "bar": "吧", "station": "档口", "table": "台", "corner": "角", "market": "档",
    "with": "配", "and": "＋", "or": "或", "of": "", "the": "", "a": "", "an": "",
    "today's": "今日", "todays": "今日", "your": "你的", "own": "自选", "build": "自选",
    "made": "现做", "order": "现做", "to": "", "upon": "", "request": "",
    "available": "", "upon request": "", "option": "选项", "options": "选项",
    "meal": "餐", "dinner": "晚餐", "lunch": "午餐", "breakfast": "早餐", "brunch": "早午餐",
    "veggies": "蔬菜", "vegetable": "蔬菜", "vegetables": "蔬菜", "veggie": "蔬菜",
    "greens": "青菜", "mixed greens": "什锦生菜", "kernels": "玉米粒",
    "wedges": "角", "wedge": "角", "rings": "圈", "sticks": "条", "link": "香肠",
    "links": "香肠", "sides": "配菜", "side": "配菜", "sauce": "酱", "sauces": "酱",
    "crinkle": "波浪", "cut": "切", "sliced": "片", "shredded": "丝",
    "cooked": "熟", "boiled": "水煮", "tossed": "拌", "toss": "拌", "glace": "酱汁",
    "famous": "招牌", "collins": "Collins", "hoch": "Hoch", "frary": "Frary",
    "faves": "招牌", "favorites": "招牌", "style": "风味", "flavor": "风味",
    "sweet": "甜", "sour": "酸", "savory": "咸香", "tangy": "酸爽", "rich": "浓郁",
    "light": "清爽", "hearty": "扎实", "smoky": "烟熏", "zesty": "开胃",
    "baked potato": "烤土豆", "sweet potato": "红薯", "red bliss": "红皮",
    "protein": "蛋白", "energy": "能量", "power": "能量", "bowl.": "碗",
    "fsp": "", "slc": "", "mwgci": "", "gf": "无麸质", "contain": "", "allergens": "",
    # —— 第二轮补充 ——
    "green": "青", "red": "红", "black": "黑", "white": "白", "yellow": "黄",
    "purple": "紫", "golden": "金黄", "brown": "棕",
    "flatbread": "烤薄饼", "cornbread": "玉米面包", "biscuit": "比司吉", "biscuits": "比司吉",
    "smoothie": "冰沙", "pulled": "手撕", "flank": "侧腹", "fennel": "茴香",
    "bokchoy": "小白菜", "bok choy": "小白菜", "cubano": "古巴三明治", "herbed": "香草",
    "whipped": "打发", "cinnamon": "肉桂", "goat": "山羊", "hot": "热", "wild": "野生",
    "chip": "片", "slider": "小汉堡", "sliders": "小汉堡", "tortellini": "意式水饺",
    "minestrone": "意式蔬菜汤", "simply": "", "sauted": "炒", "saut": "炒", "sautee": "炒",
    "poke": "夏威夷生鱼饭", "spread": "抹酱", "icing": "糖霜", "ganache": "甘纳许",
    "bundt": "邦特", "caprese": "卡普里", "machaca": "手撕牛肉", "jerk chicken": "牙买加烤鸡",
    "caribbean": "加勒比", "yukon": "育空", "grain": "粒", "long": "长",
    "nacho": "玉米片", "queso": "芝士酱", "salsa fresca": "鲜莎莎",
    "meats": "肉类", "meat sauce": "肉酱", "dumpling": "饺子", "wonton": "馄饨",
    "eggroll": "春卷", "eggplant parmesan": "帕玛森茄子", "lasagna": "千层面",
    "manicotti": "意式管面", "cannelloni": "意式酿管面", "gnocchi": "意式土豆团",
    "polenta": "玉米糊", "grits": "玉米糊", "farro": "法罗小麦", "bulgur": "碾碎小麦",
    "kale": "羽衣甘蓝", "swiss chard": "瑞士甜菜", "collards": "羽衣甘蓝叶",
    "artichoke": "洋蓟", "olives": "橄榄", "pickle": "酸黄瓜", "pickles": "酸黄瓜",
    "pretzel": "碱水面包", "pretzels": "碱水面包", "popcorn": "爆米花",
    "granola bar": "麦片棒", "energy bar": "能量棒", "protein bar": "蛋白棒",
    "boba": "珍珠", "matcha": "抹茶", "espresso": "浓缩咖啡", "latte": "拿铁",
    "blue": "蓝", "scramble": "炒蛋", "dog": "热狗", "sub": "潜艇堡", "outdoor": "户外",
    "achiote": "红木籽", "balsamic": "黑醋", "taquitos": "炸卷饼", "blackened": "焦香",
    "jack": "杰克芝士", "gumbo": "秋葵汤", "panini": "帕尼尼", "chopped": "碎",
    "dip": "蘸酱", "dandan": "担担", "dulcedeleche": "焦糖牛奶酱", "fruit": "水果",
    "shawarma": "沙威玛", "key": "青柠", "bao": "刈包", "kungpao": "宫保", "loaf": "面包条",
    "lemongrass": "香茅", "loaded": "豪华", "carneasada": "烤牛肉", "pot": "锅",
    "home": "家常", "house": "自制", "sweetener": "甜味剂", "seasonal": "当季",
    "spaghetti": "意大利面", "kimchi": "泡菜", "plain": "原味", "warm": "温热",
    "rotisserie": "烤", "tostada": "烤饼", "casserole": "焗菜", "duo": "双拼",
    "country": "乡村", "chayote": "佛手瓜", "frittata": "意式烘蛋", "thighs": "腿肉",
    "ricotta": "里科塔芝士", "oat": "燕麦", "edamame": "毛豆", "spring": "春",
    "brioche": "布里欧修", "bokchoy": "小白菜", "caulini": "花椰菜",
    "mocha": "摩卡", "aromatic": "香", "chilaquiles": "玉米片炒蛋",
    "garbanzo": "鹰嘴豆", "mongolian": "蒙古", "pomodori": "番茄", "pomodoro": "番茄",
    "pickled": "腌渍", "szechuanpeppercorns": "花椒", "eats": "食",
}

# ---------------------------------------------------------------- 翻译

_PAREN = {
    "vegetarian": "素",
    "vegan": "纯素",
    "gf": "无麸质",
    "gluten-free": "无麸质",
    "plant-based": "植物基",
    "mwgci": "",
    "contains": "",
}

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*|\d+[,.]?\d*|\"|,|\+|&|/")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_JUNK_NAME = re.compile(r"hasn'?t posted|not posted|menu may differ|no menu|closed", re.I)
_NUM_UNIT = re.compile(r"^\d+(\.\d+)?$")

_MAXP = max(len(k.split()) for k in PHRASES)


def _norm_token(t: str) -> str:
    return t.lower().replace("’", "'")


def tr(name: str) -> str:
    """把英文菜名拼成中文；实在拼不出来的部分保留原词。"""
    s = str(name or "").strip()
    if not s:
        return ""
    if _JUNK_NAME.search(s):
        return ""
    s = _CAMEL.sub(" ", s)                  # RedVelvet → Red Velvet
    s = s.replace("&", " and ")
    s = re.sub(r"(\d+)\s*\"", r"\1 寸 ", s)

    # 括号里的标记（vegan / gf / mwgci …）单独处理
    def _paren(m: re.Match[str]) -> str:
        inner = m.group(1).strip().lower()
        tag = _PAREN.get(inner)
        if tag is None:
            tag = tr(inner)
        return f"（{tag}）" if tag else " "

    s = re.sub(r"\(([^)]*)\)", _paren, s)

    tokens = _TOKEN_RE.findall(s)
    out: list[str] = []
    i = 0
    while i < len(tokens):
        low = [_norm_token(t) for t in tokens[i:i + _MAXP]]
        hit = None
        for n in range(len(low), 0, -1):
            key = " ".join(low[:n])
            if key in PHRASES and n > 1:
                hit = (n, PHRASES[key])
                break
        if hit:
            out.append(hit[1])
            i += hit[0]
            continue

        t = tokens[i]
        low = _norm_token(t)
        if low in WORDS:
            val = WORDS[low]
            if val:
                out.append(val)
        elif _NUM_UNIT.match(t):
            out.append(t)
        elif t in ",+":
            out.append("、" if t == "," else "＋")
        elif t == "/":
            out.append("或")
        elif t == '"':
            out.append("寸")
        else:
            out.append(t)          # 词表没有：品牌名之类，保留原样
        i += 1

    txt = "".join(out)
    txt = re.sub(r"＋+", "＋", txt)
    txt = re.sub(r"＋、", "、", txt)
    txt = re.sub(r"、＋", "、", txt)
    txt = re.sub(r"配＋", "配", txt)
    txt = re.sub(r"^[＋、配]+|[＋、配]+$", "", txt)
    txt = re.sub(r"\s{2,}", " ", txt)
    txt = re.sub(r"（\s*）", "", txt)
    txt = txt.strip(" -—·")
    return txt or str(name).strip()


# ---------------------------------------------------------------- 推荐

_MEAT = ("鸡", "牛", "猪", "羊", "火鸡", "鱼", "虾", "排", "培根", "香肠", "肉", "汉堡", "牛排")
_SWEET = ("蛋糕", "曲奇", "布朗尼", "派", "布丁", "甜甜圈", "玛芬", "司康", "雪葩", "芝士蛋糕", "果冻")
_SOUP = ("汤", "浓汤", "咖喱")
_LIGHT = ("沙拉", "蔬菜", "西兰花", "菠菜", "生菜", "豆腐", "藜麦", "鹰嘴豆")


def _kind(zh: str) -> str:
    if any(k in zh for k in _SWEET):
        return "sweet"
    if any(k in zh for k in _SOUP):
        return "soup"
    if any(k in zh for k in _MEAT):
        return "meat"
    if any(k in zh for k in _LIGHT):
        return "light"
    return "other"


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)


TEMPLATES_MAIN = [
    "贝尔推荐「{dish}」——肉够厚，主人练完剑吃这个才补得回来。",
    "「{dish}」最香，别又只夹一盘青菜回来。",
    "懒得挑就吃「{dish}」，热的、扎实，贝尔盯着你吃完。",
    "「{dish}」这口味贝尔自己都想尝一口……主人替贝尔吃。",
    "今天就「{dish}」吧，配点青菜，别光吃肉。",
]
TEMPLATES_BREAKFAST = [
    "早上先拿「{dish}」，热乎的，胃才醒得过来。",
    "「{dish}」配一杯酸奶，够撑到中午了。",
    "「{dish}」刚出炉的时候最好吃，主人别睡太久。",
]
TEMPLATES_LIGHT = [
    "今天素的多，「{dish}」也能吃，贝尔不逼你。",
    "「{dish}」清爽，吃完还想走动的那种。",
]
TEMPLATES_SWEET = [
    "要是只有甜点对眼，「{dessert}」拿一块，正餐还是要吃。",
    "今天甜点有「{dessert}」，主人听话吃完菜再给。",
]
# 主人的口味偏置：Collins 常见（吃腻了）扣 5，Hoch +5
HALL_BIAS = {"collins": -5, "hoch": 5}

# 主人更爱吃亚洲菜：出现在菜单里的亚洲菜越多，分数越高（最多 +6）
ASIAN_HINTS = (
    "韩式", "日式", "中式", "亚洲", "泰式", "蒙古", "越南", "河粉", "炒饭", "炒面", "炒米粉",
    "拉面", "面条", "米粉", "乌冬", "粥", "宫保", "左宗棠", "陈皮", "咖喱", "寿司", "天贝",
    "饺子", "煎饺", "锅贴", "刈包", "担担", "泡菜", "是拉差", "照烧", "味噌", "芝麻",
    "糖醋", "麻婆", "叉烧", "春卷", "馄饨", "小笼", "炸酱", "寿喜", "天妇罗",
)
NOT_ASIAN = ("牙买加", "加勒比", "墨西哥", "意式", "法式", "希腊", "地中海", "美式", "德州", "印度", "燕麦")


def is_asian(zh: str) -> bool:
    return any(h in zh for h in ASIAN_HINTS) and not any(n in zh for n in NOT_ASIAN)


def asian_bias(meals: list[dict[str, Any]]) -> int:
    """菜单里亚洲菜越多加得越多，封顶 +6。"""
    n = 0
    for m in meals or []:
        for it in m.get("items") or []:
            zh = it["zh"] if isinstance(it, dict) else str(it)
            if is_asian(zh):
                n += 1
    return min(6, 2 * n)


CLOSED_LINES = [
    "今天关门，贝尔也变不出饭来。",
    "没开，主人换一家吧。",
]


_STRONG = ("鸡胸", "鸡腿", "牛排", "牛肉", "三文鱼", "石斑鱼", "虾", "汉堡", "披萨",
           "意面", "咖喱", "肉丸", "卷饼", "塔可", "铁板", "炒饭", "焗", "炖", "烤鸡")
_WEAK = ("培根", "香肠", "热狗", "薯", "面包", "米饭", "沙拉", "汤", "蛋糕", "曲奇",
         "布丁", "果冻", "酸奶", "果酱", "蛋")


def _first_of(meals: list[dict[str, Any]], kinds: set[str]) -> str:
    for m in meals:
        for it in m["items"]:
            zh = it["zh"] if isinstance(it, dict) else str(it)
            if _kind(zh) in kinds:
                return zh
    return ""


def _main_dish(meals: list[dict[str, Any]]) -> str:
    """挑一道「像主菜」的：先肉（鸡/牛/鱼/虾），再主食型，培根香肠面包垫后。"""
    pool = [it["zh"] if isinstance(it, dict) else str(it) for m in meals for it in m["items"]]
    def ok(zh: str) -> bool:
        return (not any(w in zh for w in _WEAK)) and not zh.endswith(("吧", "台", "档"))

    meat = [zh for zh in pool if _kind(zh) == "meat" and ok(zh)]
    strong = [zh for zh in pool if any(k in zh for k in _STRONG) and ok(zh)]
    # 主人爱吃亚洲菜：亚洲的肉 / 亚洲主菜排最前
    for group in (
        [zh for zh in strong if is_asian(zh)],
        [zh for zh in meat if is_asian(zh)],
        strong,
        meat,
        [zh for zh in pool if is_asian(zh) and ok(zh)],
    ):
        if group:
            return group[0]
    return ""


def pick_recommend(hall: dict[str, Any], day: str) -> str:
    """一条女仆口吻的推荐，按「日期 + 食堂」固定，同一天不会变。"""
    meals = hall.get("meals") or []
    if not meals:
        return random.Random(_seed(day + hall["key"])).choice(CLOSED_LINES)

    labels = [m["label"] for m in meals]
    morning = any(l in ("早餐", "早午餐") for l in labels) and "晚餐" not in labels
    rng = random.Random(_seed(f"{day}|{hall['key']}|rec"))

    meat = _first_of(meals, {"meat"})
    light = _first_of(meals, {"light"})
    soup = _first_of(meals, {"soup"})
    sweet = _first_of(meals, {"sweet"})

    if morning:
        pool = TEMPLATES_BREAKFAST
    elif meat and not light:
        pool = TEMPLATES_MAIN
    else:
        pool = TEMPLATES_MAIN

    dinner_first = [m for m in meals if m["label"] in ("晚餐", "午餐")] or meals
    dish = _main_dish(dinner_first) or _main_dish(meals) or meat or light or soup or sweet
    if morning:
        dish = _first_of(meals, {"other", "meat", "light"}) or dish

    if sweet and rng.random() < 0.45:
        line = rng.choice(TEMPLATES_SWEET).format(dessert=sweet)
    elif dish:
        line = rng.choice(pool).format(dish=dish)
    else:
        line = "今天菜单有点素，贝尔勉强给你挑几样。"
    return line


def score_of(hall: dict[str, Any], day: str) -> int:
    """0-100：菜多、有肉、有甜点就高一点；同一天固定。"""
    meals = hall.get("meals") or []
    if not meals:
        return 0
    rng = random.Random(_seed(f"{day}|{hall['key']}|score"))
    base = 62 + min(len(meals), 3) * 4
    kinds = {_kind(it["zh"] if isinstance(it, dict) else str(it)) for m in meals for it in m["items"]}
    base += 6 if "meat" in kinds else 0
    base += 4 if "sweet" in kinds else 0
    base += 3 if "soup" in kinds else 0
    base += HALL_BIAS.get(str(hall.get("key") or ""), 0)
    base += asian_bias(meals)
    return max(35, min(98, base + rng.randint(-5, 9)))


def zh_items(meal: dict[str, Any]) -> list[dict[str, str]]:
    """给一顿饭的菜名加上中文（en 留着兜底/调试）。"""
    out = []
    for it in meal.get("items") or []:
        en = it["en"] if isinstance(it, dict) else str(it)
        out.append({"en": en, "zh": tr(en)})
    return out
