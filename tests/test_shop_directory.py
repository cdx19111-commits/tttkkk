from app.shop_directory import SHOP_DIRECTORY, shop_label, shop_name, shop_owner


def test_shop_directory_has_all_shops():
    assert len(SHOP_DIRECTORY) == 10
    assert shop_name("129675602") == "爱回收官方旗舰店"
    assert shop_owner("129675602") == "许杨"
    assert shop_label("129675602") == "爱回收官方旗舰店（许杨）"


def test_new_shop_and_unknown_fallback():
    assert shop_label("25565842") == "爱回收严选二手手机（王文鲜）"
    assert shop_label("not-a-shop") == "not-a-shop"
    assert shop_owner("not-a-shop") == ""
