import unittest

from pegasus_bridge.story_options import inspect_options, prepare_binding


SCENE = """local function route()
    ac.showVideo({res_id="$clip"})
    ac.createSwitchChapter({chapterId=17})
end
ac.showVideoOption({name="choice",res_id="$menu",option_items={
    {text_content="留下",click_func=function() end},
    {text_content="进宫",text_style="style2",click_func=route}
}})"""


class VideoOptionTests(unittest.TestCase):
    def test_prepare_only_changes_empty_binding(self):
        rows = inspect_options(SCENE)["options"]
        self.assertEqual(rows[0]["binding"], "empty")
        self.assertEqual(rows[1]["steps"][1]["target"], 17)
        self.assertIsNone(rows[0]["text_style"])
        self.assertEqual((rows[1]["text_style"], rows[1]["option_line"]), ("style2", 7))
        edit = prepare_binding(SCENE, "choice", 1, "留下", "route")
        self.assertEqual(edit["source"], SCENE.replace("function() end", "route"))
        self.assertEqual(edit["option"]["steps"], rows[1]["steps"])
        self.assertFalse(edit["applied"])

    def test_rejects_wrong_text_and_unknown_callback(self):
        for text, callback in [("旧标题", "route"), ("留下", "missing")]:
            with self.assertRaises(ValueError):
                prepare_binding(SCENE, "choice", 1, text, callback)
