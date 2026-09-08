<?php

namespace App\Svc;

trait Loggable
{
    public function log(): void
    {
    }
}

class Job
{
    use Loggable;
}

class Task
{
    use Loggable;

    public function log(): void
    {
    }
}
