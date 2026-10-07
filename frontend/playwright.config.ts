import {defineConfig} from '@playwright/test';
export default defineConfig({
    testDir:'tests/browser',fullyParallel:false,workers:1,
    use:{baseURL:'http://127.0.0.1:4173',headless:true,channel:'chrome',screenshot:'only-on-failure',trace:'retain-on-failure'},
    webServer:{command:'npx vite --host 127.0.0.1 --port 4173',url:'http://127.0.0.1:4173',reuseExistingServer:!process.env.CI},
});
