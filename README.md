<p align="center">
  <img src="docs/封面.gif" width="640" alt="纸箱里的猫，慢慢眨了一下眼 / a cat in a cardboard box, slowly blinking">
</p>

<h1 align="center">hachimi agent · 哈基米</h1>

<p align="center">
  一只住在你电脑里的猫。它自己醒来，自己玩，自己睡；<br>
  偶尔跑过来，叼着它在外面抓到的东西，或者给你做的小游戏。<br>
  <br>
  A cat that lives in your computer. It wakes up, plays, and naps on its own schedule,<br>
  and now and then wanders over with something it caught outside, or a little game it made for you.
</p>

<br>

## 扒窗户 · It looks out the window

好奇了，就自己上网找图，叼回家看。看到鸟，下巴咔咔咔。它梦到你说过喜欢雪，惦记了一晚上，最后还是没忍住。

When something catches its curiosity, it goes online, finds a picture, and brings it home to look at. It once dreamed that you said you love snow, thought about it all evening, and finally went to look.

<img src="docs/梦到雪.gif" width="640" alt="梦到雪，上网找了一张雪山，叼过来给你看 / dreams of snow, finds a picture of a snowy mountain, brings it to you">

## 给你做小游戏 · It makes little games for you

你说想滑雪。它用爪子拼了一个滑雪游戏，自己先玩，栽了，再叼过来给你。你一下都没按，它很困惑。

You said you wanted to go skiing. It put together a ski game with its paws, played it first, crashed, and brought it to you. You never pressed a key. It was confused.

<img src="docs/做滑雪.gif" width="640" alt="做了一个滑雪游戏，自己玩到 24 步栽了，叼给你 / builds a ski game, crashes at step 24, brings it to you">

## 偷偷记着你 · It quietly remembers you

你随口说的话，它当场假装没听见，转头记在小本本上。过几天梦到了，就去找相关的东西叼给你。不说，叼东西来，这是猫喜欢一个人的方式。

When you mention something in passing, it pretends not to hear, then writes it down in its little notebook. Days later it dreams about it and brings you something related. Not saying, just bringing things: that is how a cat shows it likes you.

<img src="docs/小本本.gif" width="640" alt="你说想去滑雪，它装睡，偷偷记在小本本上 / you say you want to ski; it pretends to sleep and writes it down">

## 你不在的时候 · While you are away

关掉电脑走了，它照样醒来、扒窗户、写日记。下次打开，门口可能多了个东西，还有一页你没看过的日记。

Close the program and walk away; it keeps living. It wakes up, looks out the window, writes in its diary. Next time you open it, there may be something left at the door, and a diary page you have not read yet.

<img src="docs/你不在的时候.gif" width="640" alt="关了 9 个小时再打开：凌晨三点半醒来看雪、写日记 / away for 9 hours: it woke at 3:30 am, looked at snow, wrote its diary">

## 什么都能唱成歌 · It sings about everything

<img src="docs/论文.png" width="640" alt="你说一直在改论文，它唱：论文啊改不完呀 / you say you have been revising your thesis all day; it sings about it">

## 不干活 · It does not work for you

让它写周报。它写了，写的是你这周加了几天班，它这周舔了几分钟毛。它会上网、会写代码、会做游戏，只是不为你做。

Ask it to write your weekly report. It does: about how much overtime you worked this week, and how many minutes it spent grooming. It can browse, write code, and build games. It just does not do it for you.

<img src="docs/周报.gif" width="640" alt="让它写周报，它写了：本周两脚兽天天加班，下周计划两脚兽少加一点班 / asked for a weekly report, it writes: the human worked overtime every day; next week's plan: less overtime for the human">

<br>

## 领养一只 · Adopt one

```
pip install git+https://github.com/jinda-li/hachimi-agent
hachimi --demo      # 先看看，不用模型、不用 key / watch a recorded cat first, no model or key needed
hachimi             # 领养你自己的 / adopt your own
```

第一次打开会先问你几件事，像去领养一只猫：它是怎么来的？你第一次伸手，它会怎样？桌上有个杯子，它会怎样？所以每只猫都不一样。它的脑袋要一个会调工具的大模型（OpenRouter、阿里云百炼、本地的 Ollama 都行），第一次打开的时候选。

The first time you open it, it asks a few questions, like adopting a cat: Where did it come from? What does it do the first time you reach out? There is a cup on the table; what does it do? So every cat is different. Its brain is any LLM with tool calling (OpenRouter, Alibaba Bailian, a local Ollama, ...), chosen on first launch.

## 偷看它的生活 · Peek into its life

打字就是跟它说话：「摸摸你」「给你开个罐罐」「窗外飞过去一只鸟」。理不理你，它自己定。

Typing is talking to it: "pet the cat", "open a can of food", "a bird just flew past the window". Whether it answers is up to the cat.

- `/日记` 它的日记 · its diary
- `/本本` 它的小本本，记着关于你的事 · its notebook about you
- `/玩具` 它做过的玩具，`/玩 名字` 你也能玩 · the toys it made, and you can play them
- `/门口` 它叼来给你的东西 · what it left at the door for you
- `/猫牌` 它是谁，你们认识多久了 · who it is, and how long you have known each other

## 游戏的人 · Homo Ludens

赫伊津哈说，人是"游戏的人"。游戏比文化更古老，人玩，不为了什么，玩本身就是意义。

今天的 AI agent 几乎都被做成"干活的人"：接任务，调工具，交结果。哈基米反过来，是一个由好奇心驱动的 agent。它有一样的本事，会上网、会读写、会写代码，但没有人给它任务。想看什么就去看，想玩了就给自己做个玩具，困了就睡。它会，只是不为你。

我们想知道：如果 AI 不用干活，它会做什么？

Johan Huizinga called us Homo Ludens, the playing human. Play is older than culture, and it needs no purpose beyond itself.

Almost every AI agent today is built as a worker: take a task, call tools, deliver a result. Hachimi goes the other way. It is a curiosity-driven agent. It has the same abilities (browsing, reading and writing files, writing code), but nobody gives it a task. It looks at whatever it wants to look at, builds itself a toy when it feels like playing, and sleeps when it is tired. It can; it just does not do it for you.

We want to find out: what would an AI do if it did not have to work?

免费，没有付费功能，MIT 许可证。做来让大家开心。 · Free, no paid features, MIT licensed. Made to make people happy.

<br>

<sub>图里的话都是一只用 Qwen 跑的猫真说过的，只挑了片段；除了名字，一个字没改。<br>
Every line in these clips is real output from a cat running on Qwen, excerpted; only its name was changed.<br>
更多 · More: [怎么用 Usage](hachimi/README.md) · [产品设计 Design](产品设计.md) · [猫的宪法 Constitution](宪法.md) · [开发 Development](开发.md)</sub>
