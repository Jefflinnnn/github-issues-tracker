## How I thought about this

### Scope
- I scoped this for a solo maintainer, a deliberately narrower reading of
  "busy open-source project."
- I read the issue trackers of vorssaint and uv and listed some of the gaps I saw 
    * vague, off-topic or tracker-style issues;
    * issues
    * mislabeling;
    * unclear bugs with no action signal
    * fixes in progress/waiting to be confirmed
- Solo maintainers' needs are small --> I built a CLI that reports in the
  terminal, where they already work.
- Report is interactive to remove a layer of
  friction between seeing a report and acting on it.
- It tracks several repositories, because one person often maintains more
  than one.

### Not built
- Web app 
- Dedicated duplication detection + PR linker
- Codebase fix suggestions --> more actionable

### Additional time 
- Decision LLM model tagging --> LLM summary  
- In-context maintainer preference calibrator
    * accuracy eval set 
- Agent skill integration

## AI collaboration log

- I used claude code agentically with myself being in the loop. 


* I first scoped the project or functionality with my own thoughts, refined the scope with claude, had claude write code, manually tested the code, manually catch bugs/inserted my own preferences, repeat until I was happy with it then have it submit a PR to GitHub for me to review. 


* It helped speed development while aligning development to my vision without letting the model over-comiting on an idea it had (avoiding goal drift) 
