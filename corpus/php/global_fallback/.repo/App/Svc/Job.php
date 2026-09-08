<?php

namespace App\Svc;

class Job
{
    public function run(): void
    {
        helper();
        local();
    }
}

function local(): void
{
}
