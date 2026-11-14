# Live deploy: VS Code -> GitHub -> Render (auto)

## Roz ka kaam (ek click)
1. VS Code me code change karke save karo, local par `python app.py` se check karo.
2. Terminal me:  `push.bat "kya badla"`   (ya  Ctrl+Shift+B  ->  "Deploy (git push -> live)")
3. 1-3 minute me live site update. Browser me Ctrl+Shift+R.

## Ek baar ka setup (Render)
1. https://render.com par GitHub se login -> **New > Blueprint** -> apna repo chuno (render.yaml khud mil jayegi).
2. Jo env vars `sync: false` hain unki value dashboard me bharo (MONGO_URI, PUBLIC_URL, ADMIN_PASSWORD, SMTP_PASS, GST_API_KEY ...). Apni `.env` se copy karo.
3. **Environment > Secret Files** me `service_account.json` upload karo, path: `/etc/secrets/service_account.json`.
4. MongoDB Atlas > Network Access me `0.0.0.0/0` allow karo (Render ka IP badalta rehta hai).
5. PUBLIC_URL me Render ka https link daalo (QR code usi par khulta hai).

## Dhyan rakho
- `.env`, service_account.json git me kabhi nahi jaate (gitignore). Live par secrets sirf Render dashboard me.
- Render ki disk ephemeral hai: har deploy par `uploads/` aur `job_resumes/` ki nayi files mit jati hain. Admin se upload ki hui files ke liye Render Disk (paid) ya cloud storage chahiye.
- Free plan par 15 min inactivity ke baad site so jati hai, pehli request 30-60 sec leti hai.
