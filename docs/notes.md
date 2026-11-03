## How I thought about this

I scoped this for a solo maintainer, a deliberately narrower reading of "busy open-source project." To see what maintainers face, I read the issue trackers of vorssaint and uv and listed the gaps: vague, off-topic or tracker-style issues; issues the reporter mislabeled; bugs with no signal of severity or priority; fixes waiting on the reporter's confirmation; and an issues tab that is slow to scan. 

A solo maintainer's needs are small, so I built a CLI that reports in the terminal, where they already work. I wanted to build an interactive report in the terminal since it removes a layer of friction between observing a report and taking action. I also wanted to have the tool track several repositories because one person often maintains more than one. 

Model provider (Anthropic, OpenAI-compatible, Bedrock, local models) and operating system (macOS, Linux, Windows) were non-negotiable for me, since the prompt says the provider may change, but I only tested on Bedrock and local due to API contraints and MacOS due to hardware constraints. Ideally I wanted a selection page like OpenClaw, but that was out of scope for a small POC like assignment.

Building a web-app did cross my mind, but having another extra website to look at, without a clear collaboration need seemed like extra friction for a solo maintainer. I kept it simple.

One thing I would consider doing differently would be to add a decision large language model classifier to add tags then have a normal reasoning language model reason over why the tags were added. It is cool for me to test out new technology and I am curious how well it would work. Setting up a decision model would have taken too much time. 

## AI collaboration log

* I used claude code agentically with myself being in the loop. 


* I first scoped the project or functionality with my own thoughts, refined the scope with claude, had claude write code, manually tested the code, manually catch bugs/inserted my own preferences, repeat until I was happy with it then have it submit a PR to GitHub for me to review. 


* It helped speed development while aligning development to my vision without letting the model over-comiting on an idea it had (avoiding goal drift) 
